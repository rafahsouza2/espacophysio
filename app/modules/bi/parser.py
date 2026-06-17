"""
Parser do relatório XLS exportado pelo sistema de agendamentos.
O arquivo é HTML disfarçado de .xls, encoding cp1252, com 25 colunas.
"""
from __future__ import annotations

import io
import json
import re
from datetime import datetime
from pathlib import Path

import pandas as pd

# ── Caminho do JSON local (fallback dev) ──────────────────────────────────────
DATA_PATH = Path(__file__).parent.parent.parent / "data" / "bi_data.json"

# ── Supabase cache (produção) ─────────────────────────────────────────────────
def _save_supabase(data: dict) -> None:
    try:
        from app.database import get_supabase_admin
        sb = get_supabase_admin()
        sb.table("bi_cache").upsert({"id": 1, "data": data}).execute()
    except Exception as e:
        print("BI SUPABASE SAVE ERROR:", repr(e))


def _load_supabase() -> dict | None:
    try:
        from app.database import get_supabase_admin
        sb = get_supabase_admin()
        res = sb.table("bi_cache").select("data").eq("id", 1).limit(1).execute()
        if res.data:
            return res.data[0]["data"]
    except Exception as e:
        print("BI SUPABASE LOAD ERROR:", repr(e))
    return None

# ── Mapeamento de status ───────────────────────────────────────────────────────
def _classify_status(s: str) -> str:
    """Normaliza qualquer variação de status para: realizado | falta | cancelado | agendado"""
    if not isinstance(s, str):
        return "agendado"
    s = s.upper().strip()
    if any(k in s for k in ["REALIZ", "ATENDID", "EXECUT"]):
        return "realizado"
    if any(k in s for k in ["FALTA", "AUSENT", "NAO COMPAREC", "NÃO COMPAREC", "FALTOU"]):
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
    return "Fisioterapia"   # default para agendas sem keyword


def _extract_profissional(agenda: str) -> str:
    """Extrai o nome do profissional do campo 'Nome da Agenda'.
    Formato: 'UNIDADE - PROFISSIONAL' ou 'UNIDADE - PROFISSIONAL - ESPECIALIDADE'
    """
    if not isinstance(agenda, str):
        return "Desconhecido"
    parts = [p.strip() for p in agenda.split(" - ")]
    if len(parts) >= 2:
        # Remove possível sufixo de especialidade do nome
        nome = parts[1]
        for keyword, _ in _ESP_MAP:
            nome = re.sub(rf"\b{keyword}\b", "", nome, flags=re.IGNORECASE).strip()
        return nome.title() if nome else parts[1].title()
    return agenda.title()


# ── Parser principal ───────────────────────────────────────────────────────────
def parse_xls(content: bytes) -> dict:
    """Lê o arquivo XLS (HTML) e retorna um dict com todos os KPIs calculados."""

    # 1. Ler como HTML
    buf = io.BytesIO(content)
    try:
        tables = pd.read_html(buf, encoding="cp1252", header=0)
    except Exception:
        buf.seek(0)
        tables = pd.read_html(buf, encoding="latin-1", header=0)

    df = tables[0]

    # 2. Normalizar nomes de colunas (remover caracteres especiais problemáticos)
    df.columns = [str(c).strip() for c in df.columns]

    # 3. Colunas essenciais com fallback por substring
    def _col(keywords: list[str]) -> str | None:
        for kw in keywords:
            matches = [c for c in df.columns if kw.lower() in c.lower()]
            if matches:
                return matches[0]
        return None

    col_dt_atend  = _col(["Data do Atendimento"])
    col_dt_nasc   = _col(["Data Nascimento"])
    col_paciente  = _col(["Nome do Paciente"])
    col_agenda    = _col(["Nome da Agenda"])
    col_unidade   = _col(["Nome da Unidade"])
    col_convenio  = _col(["Conv"])
    col_status    = _col(["Status do Atendimento"])
    col_status_ag = _col(["Status do Agendamento"])
    col_sexo      = _col(["Sexo"])
    col_valor     = _col(["Valor Cobrado"])
    col_como      = _col(["Como nos achou"])
    col_cod       = _col(["digo"])          # Nome do Código
    col_agendador = _col(["Agendou"])       # Usuário que Agendou
    col_esp_raw   = _col(["Especialidade"]) # Especialidade do profissional

    if not col_dt_atend or not col_status:
        raise ValueError("Arquivo não reconhecido: colunas obrigatórias ausentes.")

    # 4. Derivar campos
    df["_dt"] = pd.to_datetime(df[col_dt_atend], format="%d/%m/%Y", errors="coerce")
    df["_status"] = df[col_status].apply(_classify_status)
    df["_esp"]    = df[col_agenda].apply(_extract_especialidade) if col_agenda else "Outros"
    df["_prof"]   = df[col_agenda].apply(_extract_profissional)  if col_agenda else "Desconhecido"

    # Faixa etária
    if col_dt_nasc:
        df["_nasc"] = pd.to_datetime(df[col_dt_nasc], format="%d/%m/%Y", errors="coerce")
        df["_idade"] = ((df["_dt"] - df["_nasc"]).dt.days / 365.25).round(0)
    else:
        df["_idade"] = None

    # 5. Filtrar apenas registros com data válida
    df = df[df["_dt"].notna()].copy()
    total_registros = len(df)

    # ── KPIs ──────────────────────────────────────────────────────────────────
    df_real  = df[df["_status"] == "realizado"]
    df_falta = df[df["_status"] == "falta"]
    df_agend = df[df["_status"].isin(["realizado", "falta"])]  # realizados + faltas

    total_atendimentos   = len(df_real)
    total_faltas         = len(df_falta)
    total_agendados      = len(df_agend)
    total_cancelados     = (df["_status"] == "cancelado").sum()
    total_pacientes      = df_real[col_paciente].nunique() if col_paciente else 0
    pacientes_com_faltas = df_falta[col_paciente].nunique() if col_paciente else 0

    taxa_atend  = round(total_atendimentos / total_agendados * 100, 2) if total_agendados else 0
    taxa_faltas = round(total_faltas / total_agendados * 100, 2)        if total_agendados else 0

    # ── Produção financeira (Valor Cobrado) ────────────────────────────────────
    if col_valor:
        df_real = df_real.copy()
        df_real["_val"] = pd.to_numeric(df_real[col_valor], errors="coerce").fillna(0)
        total_producao = float(df_real["_val"].sum())
        ticket_medio   = round(total_producao / total_atendimentos, 2) if total_atendimentos else 0.0
    else:
        total_producao = 0.0
        ticket_medio   = 0.0

    # Convênio vs Particular split
    conv_producao = 0.0; part_producao = 0.0
    conv_pct = 0.0; part_pct = 0.0
    if col_convenio and col_valor:
        is_part = df_real[col_convenio].str.upper().str.contains("PARTICULAR", na=False)
        part_producao = float(df_real.loc[is_part,  "_val"].sum())
        conv_producao = float(df_real.loc[~is_part, "_val"].sum())
        if total_producao > 0:
            conv_pct = round(conv_producao / total_producao * 100, 1)
            part_pct = round(part_producao / total_producao * 100, 1)

    # ── Status do Agendamento granular ─────────────────────────────────────────
    status_agendamento: dict = {}
    if col_status_ag:
        sg = df[col_status_ag].fillna("Não Definido").value_counts()
        status_agendamento = {str(k): int(v) for k, v in sg.items()}

    # ── Período ───────────────────────────────────────────────────────────────
    dt_min = df["_dt"].min()
    dt_max = df["_dt"].max()

    MESES_PT = ["Jan","Fev","Mar","Abr","Mai","Jun","Jul","Ago","Set","Out","Nov","Dez"]
    if dt_min == dt_max:
        periodo_label = f"{dt_min.day:02d}/{MESES_PT[dt_min.month-1]}/{dt_min.year}"
    else:
        periodo_label = f"{MESES_PT[dt_min.month-1]}/{dt_min.year}"

    # ── Evolução diária ───────────────────────────────────────────────────────
    evo_real  = df_real.groupby(df_real["_dt"].dt.date).size()
    evo_falta = df_falta.groupby(df_falta["_dt"].dt.date).size()
    all_dates = sorted(df["_dt"].dt.date.unique())
    evo_labels = [f"{d.day:02d}/{MESES_PT[d.month-1]}" for d in all_dates]
    evo_atend  = [int(evo_real.get(d, 0))  for d in all_dates]
    evo_faltas = [int(evo_falta.get(d, 0)) for d in all_dates]

    # ── Por profissional ──────────────────────────────────────────────────────
    prof_counts = df_real.groupby("_prof").size().sort_values(ascending=False).head(15)
    profissionais = [{"nome": k, "qtde": int(v)} for k, v in prof_counts.items()]

    # ── Por especialidade ─────────────────────────────────────────────────────
    esp_real  = df_real.groupby("_esp").size().sort_values(ascending=False)
    esp_falta = df_falta.groupby("_esp").size()
    esp_agend = df_agend.groupby("_esp").size()

    especialidades = []
    for esp, real in esp_real.items():
        ag    = int(esp_agend.get(esp, real))
        falta = int(esp_falta.get(esp, 0))
        taxa  = round(real / ag * 100, 2) if ag else 0
        especialidades.append({
            "cat": esp, "agendados": ag, "realizados": int(real),
            "faltas": falta,
            "taxa_atend": taxa,
            "taxa_faltas": round(falta / ag * 100, 2) if ag else 0,
        })

    # ── Por unidade ───────────────────────────────────────────────────────────
    unidades = []
    if col_unidade:
        un_real  = df_real.groupby(col_unidade).size()
        un_agend = df_agend.groupby(col_unidade).size()
        for u, r in un_real.sort_values(ascending=False).items():
            ag = int(un_agend.get(u, r))
            unidades.append({"nome": u, "agendados": ag, "realizados": int(r)})

    # ── Por convênio (com produção financeira) ────────────────────────────────
    convenios = []
    if col_convenio:
        grp_cols = {"qtde": (col_paciente if col_paciente else col_convenio, "count")}
        if col_valor:
            grp_cols["producao"] = ("_val", "sum")
        cv_grp = df_real.groupby(col_convenio).agg(**grp_cols)
        sort_by = "producao" if col_valor else "qtde"
        cv_grp = cv_grp.sort_values(sort_by, ascending=False).head(15)
        for k, row in cv_grp.iterrows():
            qtde = int(row["qtde"])
            prod = float(row.get("producao", 0))
            convenios.append({
                "nome":     k,
                "qtde":     qtde,
                "producao": round(prod, 2),
                "ticket":   round(prod / qtde, 2) if qtde and prod else 0.0,
                "pct":      round(prod / total_producao * 100, 1) if total_producao else 0.0,
            })

    # ── Por tipo de atendimento (Nome do Código) ──────────────────────────────
    tipos = []
    if col_cod:
        grp_t = {"qtde": (col_paciente if col_paciente else col_cod, "count")}
        if col_valor:
            grp_t["producao"] = ("_val", "sum")
        tp_grp = df_real.groupby(col_cod).agg(**grp_t).sort_values(
            "producao" if col_valor else "qtde", ascending=False
        ).head(20)
        for k, row in tp_grp.iterrows():
            qtde = int(row["qtde"])
            prod = float(row.get("producao", 0))
            tipos.append({
                "nome":     str(k),
                "qtde":     qtde,
                "producao": round(prod, 2),
                "ticket":   round(prod / qtde, 2) if qtde and prod else 0.0,
                "pct":      round(prod / total_producao * 100, 1) if total_producao else 0.0,
            })

    # ── Por agendador (Usuário que Agendou) ───────────────────────────────────
    agendadores = []
    if col_agendador:
        ag_total_df = df.groupby(col_agendador).size().rename("total_ag")
        grp_a = {"finalizados": (col_paciente if col_paciente else col_agendador, "count")}
        if col_valor:
            grp_a["producao"] = ("_val", "sum")
        ag_grp = df_real.groupby(col_agendador).agg(**grp_a).sort_values(
            "producao" if col_valor else "finalizados", ascending=False
        )
        for k, row in ag_grp.iterrows():
            fin  = int(row["finalizados"])
            prod = float(row.get("producao", 0))
            total_ag = int(ag_total_df.get(k, fin))
            agendadores.append({
                "nome":       str(k),
                "agendamentos": total_ag,
                "finalizados":  fin,
                "producao":   round(prod, 2),
                "ticket":     round(prod / fin, 2) if fin and prod else 0.0,
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
        df_real = df_real.copy()
        df_real["_faixa"] = pd.cut(df_real["_idade"], bins=bins, labels=labels, right=False)
        for lbl in labels:
            faixa_etaria[lbl] = int((df_real["_faixa"] == lbl).sum())

    # ── Novas entradas (primeira vez do paciente no período) ──────────────────
    novas_entradas = []
    if col_paciente and col_dt_nasc and col_convenio:
        # Pacientes sem histórico anterior ao período = não identificável sem histórico
        # Listamos pacientes únicos com menor data de criação de agendamento
        col_criacao = _col(["criação", "criacao", "Criação"])
        if col_criacao:
            df["_dt_criacao"] = pd.to_datetime(df[col_criacao], format="%d/%m/%Y", errors="coerce")
            novos = df[df["_dt_criacao"] >= df["_dt"].min()][[col_paciente, "_idade", col_convenio]].drop_duplicates(subset=[col_paciente]).head(50)
            novas_entradas = [
                {"nome": row[col_paciente], "idade": int(row["_idade"]) if pd.notna(row["_idade"]) else None,
                 "porta": row[col_convenio]}
                for _, row in novos.iterrows()
            ]

    # ── Como nos achou ────────────────────────────────────────────────────────
    como_achou = {}
    if col_como:
        ca = df_real[col_como].value_counts().head(10)
        como_achou = {str(k): int(v) for k, v in ca.items()}

    # ── Resultado final ───────────────────────────────────────────────────────
    result = {
        "atualizado_em": datetime.now().isoformat(),
        "total_registros": total_registros,
        "periodo": {
            "inicio": dt_min.strftime("%d/%m/%Y"),
            "fim":    dt_max.strftime("%d/%m/%Y"),
            "label":  periodo_label,
        },
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
        },
        "evolucao_diaria": {
            "labels":       evo_labels,
            "atendimentos": evo_atend,
            "faltas":       evo_faltas,
        },
        "profissionais":      profissionais,
        "especialidades":     especialidades,
        "unidades":           unidades,
        "convenios":          convenios,
        "tipos":              tipos,
        "agendadores":        agendadores,
        "status_agendamento": status_agendamento,
        "genero":             genero,
        "faixa_etaria":       faixa_etaria,
        "novas_entradas":     novas_entradas,
        "como_achou":         como_achou,
    }

    # Salvar: Supabase primeiro, disco como fallback dev
    _save_supabase(result)
    try:
        DATA_PATH.parent.mkdir(parents=True, exist_ok=True)
        DATA_PATH.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass

    return result


def load_saved() -> dict | None:
    """Carrega o último processamento: Supabase (prod) ou disco (dev)."""
    # Tenta Supabase primeiro
    data = _load_supabase()
    if data:
        return data
    # Fallback: arquivo local
    if DATA_PATH.exists():
        try:
            return json.loads(DATA_PATH.read_text(encoding="utf-8"))
        except Exception:
            pass
    return None
