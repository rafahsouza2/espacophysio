"""
Parser do relatório XLS exportado pelo sistema de agendamentos.
O arquivo é HTML disfarçado de .xls, encoding cp1252, com 25 colunas.
Baseado na lógica do WandaHorta (parseXLS.ts) portada para Python.
"""
from __future__ import annotations

import io
import json
import re
from datetime import datetime
from pathlib import Path

import pandas as pd

# ── Meta padrão (sobrescrita pela config em parse_xls) ───────────────────────
META          = 1_000_000     # fallback estático usado só nos backfills antigos
META_DIARIA   = META / 22

# ── Caminho do JSON local (fallback dev) ──────────────────────────────────────
DATA_PATH = Path(__file__).parent.parent.parent / "data" / "bi_data.json"

# ── Supabase — multi-período ──────────────────────────────────────────────────
def _save_supabase(data: dict) -> str | None:
    """Salva no Supabase. Retorna None se ok, mensagem de erro se falhou."""
    try:
        from app.database import get_supabase_admin
        sb = get_supabase_admin()
        k = data["kpis"]
        sb.table("bi_reports").upsert({
            "period_key":         data["period_key"],
            "periodo_label":      data["periodo"]["label"],
            "periodo_inicio":     data["periodo"]["inicio"],
            "periodo_fim":        data["periodo"]["fim"],
            "total_registros":    int(data["total_registros"]),
            "total_atendimentos": int(k["total_atendimentos"]),
            "total_producao":     float(k["total_producao"]),
            "data":               data,
        }).execute()
        return None
    except Exception as e:
        msg = repr(e)
        print("BI SUPABASE SAVE ERROR:", msg)
        return msg


def _load_supabase(period_key: str | None = None) -> dict | None:
    try:
        from app.database import get_supabase_admin
        sb = get_supabase_admin()
        q = sb.table("bi_reports").select("data")
        if period_key:
            q = q.eq("period_key", period_key)
        else:
            q = q.order("period_key", desc=True).limit(1)
        res = q.execute()
        if res.data:
            return res.data[0]["data"]
    except Exception as e:
        print("BI SUPABASE LOAD ERROR:", repr(e))
    return None


def list_reports() -> list[dict]:
    """Lista todos os relatórios importados com metadados (sem o JSON completo)."""
    try:
        from app.database import get_supabase_admin
        sb = get_supabase_admin()
        res = sb.table("bi_reports").select(
            "period_key, periodo_label, periodo_inicio, periodo_fim, "
            "total_registros, total_atendimentos, total_producao, updated_at"
        ).order("period_key", desc=True).execute()
        return res.data or []
    except Exception as e:
        print("BI SUPABASE LIST ERROR:", repr(e))
    return []


# ── Mapeamento de status ───────────────────────────────────────────────────────
def _classify_status(s: str) -> str:
    """Normaliza qualquer variação de status para: realizado | falta | cancelado | agendado"""
    if not isinstance(s, str):
        return "agendado"
    s = s.upper().strip()
    if any(k in s for k in ["REALIZ", "ATENDID", "EXECUT", "FINALIZ", "FATURAD", "CONCLU"]):
        return "realizado"
    if any(k in s for k in ["FALTA", "AUSENT", "NAO COMPAREC", "NÃO COMPAREC", "FALTOU", "DESMARCOU", "DESISTIU", "DESMARCADO"]):
        return "falta"
    if any(k in s for k in ["CANCEL", "DESMARC"]):
        return "cancelado"
    return "agendado"


# ── Extrai especialidade do Nome da Agenda ────────────────────────────────────
_ESP_MAP = [
    ("PILATES",       "Pilates"),
    ("RPG",           "RPG"),
    ("ACUPUNTURA",    "Acupuntura"),
    ("PELVIC",        "Fisioterapia Pélvica"),
    ("FISIO",         "Fisioterapia"),
    ("YOGA",          "Yoga"),
    ("MASSAGEM",      "Massagem"),
    ("PSICO",         "Psicologia"),
    ("FONO",          "Fonoaudiologia"),
    ("TERAPIA OCUP",  "Terapia Ocupacional"),
    ("OSTEO",         "Osteopatia"),
    ("TRIAGEM",       "Triagem"),
    ("RETRIAGEM",     "Retriagem"),
    ("NUTRIÇÃO",      "Nutrição"),
    ("NUTRI",         "Nutrição"),
    ("EDUCADOR FISIC","Educador Físico"),
]

def _extract_especialidade(agenda: str) -> str:
    if not isinstance(agenda, str):
        return "Outros"
    upper = agenda.upper()
    for keyword, label in _ESP_MAP:
        if keyword in upper:
            return label
    return "Fisioterapia"


def _extract_profissional(agenda: str) -> str:
    """Extrai o nome do profissional do campo 'Nome da Agenda'.
    Formato: 'UNIDADE - PROFISSIONAL' ou 'UNIDADE - PROFISSIONAL - ESPECIALIDADE'
    """
    if not isinstance(agenda, str):
        return "Desconhecido"
    parts = [p.strip() for p in agenda.split(" - ")]
    if len(parts) >= 2:
        nome = parts[1]
        for keyword, _ in _ESP_MAP:
            nome = re.sub(rf"\b{keyword}\b", "", nome, flags=re.IGNORECASE).strip()
        return nome.title() if nome else parts[1].title()
    return agenda.title()


# ── Chaves do cross-matrix (igual WandaHorta) ────────────────────────────────
_KEYS_AG   = ["", "Confirmada", "Não Confirmado", "Faltou", "Desmarcou", "Desistiu"]
_LABELS_AG = ["Sem Resposta", "Confirmada", "Não Confirmado", "Faltou", "Desmarcou", "Desistiu"]
_KEYS_AT   = ["FINALIZADA", "CONSULTA MARCADA", "EM ANDAMENTO", "FILA DE ESPERA", "FILA DE SENHAS"]
_LABELS_AT = ["Finalizada", "C. Marcada", "Em Andamento", "Fila Espera", "Fila Senhas"]


# ── Parser principal ───────────────────────────────────────────────────────────
def parse_xls(content: bytes) -> dict:
    """Lê XLS (HTML disfarçado) ou XLSX real e retorna dict com todos os KPIs."""
    # Carrega meta configurada pelo usuário
    from app.modules.bi.config import get_bi_config
    _cfg        = get_bi_config()
    meta        = float(_cfg["meta_mensal"])
    dias_uteis  = float(_cfg["dias_uteis_mes"])
    meta_diaria = meta / dias_uteis

    # 1. Detectar formato e ler
    buf = io.BytesIO(content)
    is_xlsx = content[:2] == b'PK'   # XLSX é ZIP

    df = None
    if is_xlsx:
        # XLSX real — usa openpyxl
        try:
            df = pd.read_excel(buf, header=0, engine="openpyxl")
        except Exception as e:
            raise ValueError(f"Erro ao ler XLSX: {e}")
    else:
        # XLS HTML-disfarçado — usa read_html
        for enc in ("cp1252", "latin-1", "utf-8"):
            try:
                buf.seek(0)
                tables = pd.read_html(buf, encoding=enc, header=0)
                # Pega a maior tabela com as colunas esperadas
                df = max(tables, key=lambda t: len(t))
                break
            except Exception:
                continue
        if df is None:
            raise ValueError("Não foi possível ler o arquivo. Verifique se é o export correto.")

    df.columns = [str(c).strip() for c in df.columns]

    # 2. Detectar colunas por keyword
    def _col(keywords: list[str]) -> str | None:
        for kw in keywords:
            matches = [c for c in df.columns if kw.lower() in c.lower()]
            if matches:
                return matches[0]
        return None

    col_dt_atend  = _col(["Data do Atendimento"])
    col_dt_nasc   = _col(["Data Nascimento", "Data de Nascimento"])
    col_paciente  = _col(["Nome do Paciente"])
    col_agenda    = _col(["Nome da Agenda"])
    col_unidade   = _col(["Nome da Unidade"])
    col_convenio  = _col(["Nome do Conv", "Conv"])          # "Nome do Convênio" ou "Convênio"
    col_status    = _col(["Status do Atendimento"])
    col_status_ag = _col(["Status do Agendamento"])
    col_sexo      = _col(["Sexo"])
    col_valor     = _col(["Valor Cobrado"])
    col_como      = _col(["Como nos achou"])
    col_cod       = _col(["Tipo do Atendimento", "digo", "Procedimento"])  # "Tipo do Atendimento" no iGut
    col_agendador = _col(["Agendou", "Usu"])                # "Usuário que Agendou"
    col_prof_raw  = _col(["Profissional"])                   # coluna direta de profissional
    col_esp_raw   = _col(["Especialidade"])                  # coluna direta de especialidade

    if not col_dt_atend or not col_status:
        raise ValueError("Arquivo não reconhecido: colunas obrigatórias ausentes.")

    # 3. Derivar campos
    # Aceita tanto "02/05/2026" (XLS) quanto datetime do Excel (XLSX)
    df["_dt"] = pd.to_datetime(df[col_dt_atend], dayfirst=True, errors="coerce")
    df["_status"] = df[col_status].apply(_classify_status)
    # Especialidade: prefere coluna direta; senão extrai de Nome da Agenda
    if col_esp_raw:
        df["_esp"] = df[col_esp_raw].fillna("Outros").astype(str).str.strip()
    elif col_agenda:
        df["_esp"] = df[col_agenda].apply(_extract_especialidade)
    else:
        df["_esp"] = "Outros"

    # Profissional: prefere coluna direta, senão extrai de Nome da Agenda
    if col_prof_raw:
        df["_prof"] = df[col_prof_raw].astype(str).str.strip().str.title()
    elif col_agenda:
        df["_prof"] = df[col_agenda].apply(_extract_profissional)
    else:
        df["_prof"] = "Desconhecido"

    # Valor monetário adicionado ao df completo desde já
    if col_valor:
        df["_val"] = pd.to_numeric(df[col_valor], errors="coerce").fillna(0.0)
    else:
        df["_val"] = 0.0

    # Faixa etária
    if col_dt_nasc:
        df["_nasc"]  = pd.to_datetime(df[col_dt_nasc], format="%d/%m/%Y", errors="coerce")
        df["_idade"] = ((df["_dt"] - df["_nasc"]).dt.days / 365.25).round(0)
    else:
        df["_idade"] = None

    # Status raw para cross-matrix
    df["_stag_raw"] = (df[col_status_ag].fillna("").astype(str).str.strip()
                       if col_status_ag else "")
    df["_stat_raw"] = df[col_status].fillna("").astype(str).str.strip().str.upper()

    # 4. Filtrar linhas com data válida
    df = df[df["_dt"].notna()].copy()
    total_registros = len(df)

    # ── KPIs de status ────────────────────────────────────────────────────────
    df_real  = df[df["_status"] == "realizado"].copy()
    df_falta = df[df["_status"] == "falta"].copy()
    df_agend = df[df["_status"].isin(["realizado", "falta"])].copy()

    total_atendimentos   = int(len(df_real))
    total_faltas         = int(len(df_falta))
    total_agendados      = int(len(df_agend))
    total_cancelados     = int((df["_status"] == "cancelado").sum())
    total_pacientes      = int(df_real[col_paciente].nunique()) if col_paciente else 0
    pacientes_com_faltas = int(df_falta[col_paciente].nunique()) if col_paciente else 0

    taxa_atend  = round(total_atendimentos / total_agendados * 100, 2) if total_agendados else 0.0
    taxa_faltas = round(total_faltas / total_agendados * 100, 2)        if total_agendados else 0.0

    # ── Produção financeira ───────────────────────────────────────────────────
    total_producao = float(df_real["_val"].sum())
    ticket_medio   = round(total_producao / total_atendimentos, 2) if total_atendimentos else 0.0

    conv_producao = 0.0; part_producao = 0.0
    conv_pct = 0.0;      part_pct = 0.0
    if col_convenio:
        is_part = df_real[col_convenio].str.upper().str.contains("PARTICULAR", na=False)
        part_producao = float(df_real.loc[is_part,  "_val"].sum())
        conv_producao = float(df_real.loc[~is_part, "_val"].sum())
        if total_producao > 0:
            conv_pct = round(conv_producao / total_producao * 100, 1)
            part_pct = round(part_producao / total_producao * 100, 1)

    # ── Metas ─────────────────────────────────────────────────────────────────
    alcance_meta = round(total_producao / meta * 100, 1)

    # ── Status do Agendamento / Atendimento (granular) ────────────────────────
    status_agendamento: dict = {}
    if col_status_ag:
        sg = df[col_status_ag].fillna("").astype(str).str.strip().value_counts()
        status_agendamento = {str(k): int(v) for k, v in sg.items()}

    status_atendimento: dict = {}
    sat = df[col_status].fillna("").astype(str).str.strip().str.upper().value_counts()
    status_atendimento = {str(k): int(v) for k, v in sat.items()}

    # ── Cross-matrix agendamento × atendimento ────────────────────────────────
    cross_counts = df.groupby(["_stag_raw", "_stat_raw"]).size()

    cross_matrix = []
    for ag_key in _KEYS_AG:
        row_vals = []
        for at_key in _KEYS_AT:
            count = int(cross_counts.get((ag_key, at_key), 0))
            row_vals.append(count)
        cross_matrix.append(row_vals)

    cross_totals_ag = [int((df["_stag_raw"] == k).sum()) for k in _KEYS_AG]
    cross_totals_at = [int((df["_stat_raw"] == k).sum()) for k in _KEYS_AT]

    cross_data = {
        "labels_ag": _LABELS_AG,
        "labels_at": _LABELS_AT,
        "matrix":    cross_matrix,
        "totals_ag": cross_totals_ag,
        "totals_at": cross_totals_at,
    }

    # ── Período ───────────────────────────────────────────────────────────────
    dt_min = df["_dt"].min()
    dt_max = df["_dt"].max()
    MESES_PT = ["Jan","Fev","Mar","Abr","Mai","Jun","Jul","Ago","Set","Out","Nov","Dez"]
    if dt_min == dt_max:
        periodo_label = f"{dt_min.day:02d}/{MESES_PT[dt_min.month-1]}/{dt_min.year}"
    else:
        periodo_label = f"{MESES_PT[dt_min.month-1]}/{dt_min.year}"

    # ── Evolução diária com produção acumulada ────────────────────────────────
    all_dates = sorted(df["_dt"].dt.date.unique())
    dias = len(all_dates)
    media_diaria = round(total_producao / dias, 2) if dias else 0.0

    evo_real_cnt  = df_real.groupby(df_real["_dt"].dt.date).size()
    evo_falta_cnt = df_falta.groupby(df_falta["_dt"].dt.date).size()
    evo_prod_dia  = df_real.groupby(df_real["_dt"].dt.date)["_val"].sum()

    acum_prod = 0.0
    melhor_dia = "—"
    melhor_dia_prod = 0.0
    evo_labels = []; evo_atend = []; evo_faltas = []; evo_prod = []; evo_acum = []; evo_meta_pct = []

    for d in all_dates:
        lbl  = f"{d.day:02d}/{MESES_PT[d.month-1]}"
        prod = round(float(evo_prod_dia.get(d, 0.0)), 2)
        acum_prod = round(acum_prod + prod, 2)
        meta_pct  = round(prod / meta_diaria * 100, 1) if meta_diaria else 0.0

        evo_labels.append(lbl)
        evo_atend.append(int(evo_real_cnt.get(d, 0)))
        evo_faltas.append(int(evo_falta_cnt.get(d, 0)))
        evo_prod.append(prod)
        evo_acum.append(acum_prod)
        evo_meta_pct.append(meta_pct)

        if prod > melhor_dia_prod:
            melhor_dia_prod = prod
            melhor_dia = lbl

    # ── Por profissional (completo: total + final + prod) ─────────────────────
    prof_all        = df.groupby("_prof").size()
    prof_final_cnt  = df_real.groupby("_prof").size()
    prof_prod_val   = df_real.groupby("_prof")["_val"].sum()

    profissionais = []
    for nome in prof_all.index:
        total = int(prof_all[nome])
        final = int(prof_final_cnt.get(nome, 0))
        prod  = round(float(prof_prod_val.get(nome, 0.0)), 2)
        profissionais.append({
            "nome":       nome,
            "qtde":       total,
            "total":      total,
            "final":      final,
            "marcado":    total - final,
            "prod":       prod,
            "taxa_final": round(final / total * 100, 1) if total else 0.0,
        })
    profissionais.sort(key=lambda p: p["prod"] if p["prod"] > 0 else p["final"], reverse=True)
    profissionais = profissionais[:15]

    # ── Por especialidade ─────────────────────────────────────────────────────
    esp_real  = df_real.groupby("_esp").size().sort_values(ascending=False)
    esp_falta = df_falta.groupby("_esp").size()
    esp_agend = df_agend.groupby("_esp").size()
    esp_prod  = df_real.groupby("_esp")["_val"].sum()

    especialidades = []
    for esp, real in esp_real.items():
        ag    = int(esp_agend.get(esp, real))
        falta = int(esp_falta.get(esp, 0))
        prod  = round(float(esp_prod.get(esp, 0.0)), 2)
        taxa  = round(real / ag * 100, 2) if ag else 0.0
        especialidades.append({
            "cat":        esp,
            "agendados":  ag,
            "realizados": int(real),
            "faltas":     falta,
            "prod":       prod,
            "taxa_atend": taxa,
            "taxa_faltas":round(falta / ag * 100, 2) if ag else 0.0,
        })

    # ── Por unidade ───────────────────────────────────────────────────────────
    unidades = []
    if col_unidade:
        un_real  = df_real.groupby(col_unidade).size()
        un_agend = df_agend.groupby(col_unidade).size()
        for u, r in un_real.sort_values(ascending=False).items():
            ag = int(un_agend.get(u, r))
            unidades.append({"nome": u, "agendados": ag, "realizados": int(r)})

    # ── Por convênio ──────────────────────────────────────────────────────────
    convenios = []
    if col_convenio:
        cv_grp = df_real.groupby(col_convenio).agg(
            qtde=("_val", "count"),
            producao=("_val", "sum"),
        ).sort_values("producao", ascending=False).head(15)
        for k, row in cv_grp.iterrows():
            qtde = int(row["qtde"])
            prod = round(float(row["producao"]), 2)
            convenios.append({
                "nome":     k,
                "qtde":     qtde,
                "final":    qtde,
                "producao": prod,
                "pct":      round(prod / total_producao * 100, 1) if total_producao else 0.0,
                "ticket":   round(prod / qtde, 2) if qtde else 0.0,
            })

    # ── Por tipo de atendimento (Nome do Código) ──────────────────────────────
    tipos = []
    if col_cod:
        tp_grp = df_real.groupby(col_cod).agg(
            qtde=("_val", "count"),
            producao=("_val", "sum"),
        ).sort_values("producao", ascending=False).head(20)
        for k, row in tp_grp.iterrows():
            qtde = int(row["qtde"])
            prod = round(float(row["producao"]), 2)
            tipos.append({
                "nome":     str(k),
                "qtde":     qtde,
                "final":    qtde,
                "producao": prod,
                "pct":      round(prod / total_producao * 100, 1) if total_producao else 0.0,
                "ticket":   round(prod / qtde, 2) if qtde else 0.0,
            })

    # ── Por atendente (Usuário que Agendou) ───────────────────────────────────
    atendentes = []
    if col_agendador:
        ag_total = df.groupby(col_agendador).size()
        at_grp   = df_real.groupby(col_agendador).agg(
            finalizados=("_val", "count"),
            producao=("_val", "sum"),
        ).sort_values("finalizados", ascending=False)
        for k, row in at_grp.iterrows():
            fin  = int(row["finalizados"])
            prod = round(float(row["producao"]), 2)
            total_ag = int(ag_total.get(k, fin))
            atendentes.append({
                "nome":         str(k),
                "agendamentos": total_ag,
                "finalizados":  fin,
                "marcado":      total_ag - fin,
                "producao":     prod,
                "taxa_final":   round(fin / total_ag * 100, 1) if total_ag else 0.0,
                "ticket":       round(prod / fin, 2) if fin else 0.0,
            })

    # ── Por sexo ──────────────────────────────────────────────────────────────
    genero = {"feminino": 0, "masculino": 0}
    if col_sexo:
        sx = df_real[col_sexo].str.lower().value_counts()
        genero["feminino"]  = int(sx.get("feminino",  sx.filter(like="fem").sum()))
        genero["masculino"] = int(sx.get("masculino", sx.filter(like="masc").sum()))

    # ── Faixa etária ──────────────────────────────────────────────────────────
    faixa_etaria = {}
    if col_dt_nasc:
        bins   = [0, 18, 30, 45, 60, 80, 200]
        labels = ["<18", "18-29", "30-44", "45-59", "60-79", "80+"]
        df_real["_faixa"] = pd.cut(df_real["_idade"], bins=bins, labels=labels, right=False)
        for lbl in labels:
            faixa_etaria[lbl] = int((df_real["_faixa"] == lbl).sum())

    # ── Como nos achou ────────────────────────────────────────────────────────
    como_achou = {}
    if col_como:
        ca = df_real[col_como].value_counts().head(10)
        como_achou = {str(k): int(v) for k, v in ca.items()}

    # ── Chave de período ──────────────────────────────────────────────────────
    period_key = f"{dt_min.year}-{dt_min.month:02d}"

    # ── Resultado final ───────────────────────────────────────────────────────
    result = {
        "atualizado_em": datetime.now().isoformat(),
        "period_key":    period_key,
        "total_registros": total_registros,
        "periodo": {
            "inicio": dt_min.strftime("%d/%m/%Y"),
            "fim":    dt_max.strftime("%d/%m/%Y"),
            "label":  periodo_label,
        },
        # ── Metas ──
        "meta":         meta,
        "meta_diaria":  round(meta_diaria, 2),
        "alcance_meta": alcance_meta,
        "dias":         dias,
        "media_diaria": media_diaria,
        "melhor_dia":   melhor_dia,
        "melhor_dia_prod": round(melhor_dia_prod, 2),
        # ── KPIs ──
        "kpis": {
            "total_atendimentos":   total_atendimentos,
            "total_pacientes":      total_pacientes,
            "taxa_atendimento":     taxa_atend,
            "taxa_faltas":          taxa_faltas,
            "total_agendados":      total_agendados,
            "total_faltas":         total_faltas,
            "total_cancelados":     int(total_cancelados),
            "pacientes_com_faltas": int(pacientes_com_faltas),
            "total_producao":       round(total_producao, 2),
            "ticket_medio":         round(ticket_medio, 2),
            "conv_producao":        round(conv_producao, 2),
            "part_producao":        round(part_producao, 2),
            "conv_pct":             conv_pct,
            "part_pct":             part_pct,
            "alcance_meta":         alcance_meta,
        },
        # ── Evolução diária ──
        "evolucao_diaria": {
            "labels":       evo_labels,
            "atendimentos": evo_atend,
            "faltas":       evo_faltas,
            "prod":         evo_prod,
            "acum":         evo_acum,
            "meta_pct":     evo_meta_pct,
        },
        # ── Dimensões ──
        "profissionais":      profissionais,
        "especialidades":     especialidades,
        "unidades":           unidades,
        "convenios":          convenios,
        "tipos":              tipos,
        "atendentes":         atendentes,
        # ── Status ──
        "status_agendamento": status_agendamento,
        "status_atendimento": status_atendimento,
        "cross_data":         cross_data,
        # ── Extras ──
        "genero":       genero,
        "faixa_etaria": faixa_etaria,
        "como_achou":   como_achou,
    }

    # Salvar: Supabase primeiro, disco como fallback dev
    save_error = _save_supabase(result)
    result["_save_error"] = save_error
    try:
        DATA_PATH.parent.mkdir(parents=True, exist_ok=True)
        DATA_PATH.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as e:
        print("BI DISK SAVE ERROR:", repr(e))

    return result


def load_saved(period_key: str | None = None) -> dict | None:
    """Carrega relatório: por period_key ou o mais recente. Supabase primeiro, disco como fallback."""
    data = _load_supabase(period_key)
    if data:
        return data
    if DATA_PATH.exists():
        try:
            return json.loads(DATA_PATH.read_text(encoding="utf-8"))
        except Exception:
            pass
    return None


def clear_saved(period_key: str | None = None) -> None:
    """Remove relatório do Supabase (por period_key) e do disco."""
    try:
        from app.database import get_supabase_admin
        sb = get_supabase_admin()
        q = sb.table("bi_reports").delete()
        if period_key:
            q = q.eq("period_key", period_key)
        else:
            q = q.neq("period_key", "")
        q.execute()
    except Exception as e:
        print("BI SUPABASE CLEAR ERROR:", repr(e))
    try:
        DATA_PATH.unlink(missing_ok=True)
    except Exception:
        pass
