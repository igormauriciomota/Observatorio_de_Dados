from datetime import date, datetime
from pathlib import Path

import pandas as pd


REQUIRED = ["data", "cliente", "produto", "categoria", "quantidade", "preco_unitario", "custo_unitario"]


def _number(value):
    if pd.isna(value):
        return float("nan")
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().replace("R$", "").replace(" ", "")
    if "," in text:
        text = text.replace(".", "").replace(",", ".")
    try:
        return float(text)
    except ValueError:
        return float("nan")


def _date(value):
    if isinstance(value, (pd.Timestamp, datetime, date)):
        return pd.Timestamp(value).normalize()
    text = str(value).strip()
    # Datas explícitas impedem que 03/04 seja interpretado de duas formas.
    for fmt in ("%d/%m/%Y", "%Y-%m-%d"):
        try:
            return pd.Timestamp(datetime.strptime(text, fmt))
        except (ValueError, TypeError):
            pass
    return pd.NaT


def load_sales(path: Path):
    try:
        if path.suffix == ".csv":
            try:
                df = pd.read_csv(path, sep=None, engine="python", dtype=str, encoding="utf-8-sig")
            except UnicodeDecodeError:
                df = pd.read_csv(path, sep=None, engine="python", dtype=str, encoding="cp1252")
        else:
            df = pd.read_excel(path, dtype=object, engine="openpyxl")
    except Exception as exc:
        raise ValueError("Não foi possível ler o arquivo. Confira se é uma planilha válida.") from exc
    df.columns = [str(col).strip().lower() for col in df.columns]
    missing = [name for name in REQUIRED if name not in df.columns]
    if missing:
        raise ValueError("Colunas ausentes: " + ", ".join(missing) + ". Baixe o modelo e siga os nomes das colunas.")
    if df.empty:
        raise ValueError("A planilha está vazia.")
    if len(df) > 10000:
        raise ValueError("Este protótipo aceita até 10.000 linhas por planilha.")
    df = df[REQUIRED].copy()
    issues = []
    for column in ("cliente", "produto", "categoria"):
        df[column] = df[column].astype("string").fillna("").astype(str).str.strip()
        bad = df.index[df[column].eq("")].tolist()
        if bad:
            issues.append(f"{column}: linhas {', '.join(str(i + 2) for i in bad[:5])}")
    df["data"] = df["data"].map(_date)
    bad = df.index[df["data"].isna()].tolist()
    if bad:
        issues.append(f"data inválida: linhas {', '.join(str(i + 2) for i in bad[:5])}")
    for column in ("quantidade", "preco_unitario", "custo_unitario"):
        df[column] = df[column].map(_number)
        bad = df.index[~df[column].map(lambda v: pd.notna(v) and 0 <= v < float("inf"))].tolist()
        if bad:
            issues.append(f"{column} inválido ou negativo: linhas {', '.join(str(i + 2) for i in bad[:5])}")
    if issues:
        raise ValueError("Corrija a planilha: " + "; ".join(issues[:7]) + ".")
    duplicate_count = int(df.duplicated().sum())
    zero_prices = int(df["preco_unitario"].eq(0).sum())
    df["receita"] = df["quantidade"] * df["preco_unitario"]
    df["custo"] = df["quantidade"] * df["custo_unitario"]
    df["resultado"] = df["receita"] - df["custo"]
    return df, {"rows": len(df), "duplicates": duplicate_count, "zero_prices": zero_prices}


def analyze(df, quality):
    revenue = float(df["receita"].sum())
    costs = float(df["custo"].sum())
    profit = revenue - costs
    margin = profit / revenue * 100 if revenue else None
    period = df["data"].dt.to_period("M").astype(str)
    by_month = df.assign(periodo=period).groupby("periodo", sort=True)[["receita", "custo", "resultado"]].sum()
    by_category = df.groupby("categoria")[["receita", "resultado"]].sum().sort_values("receita", ascending=False)
    by_client = df.groupby("cliente")["receita"].sum().sort_values(ascending=False)
    by_day = df.assign(dia=df["data"].dt.strftime("%d/%m/%Y")).groupby("dia", sort=False).agg(
        receita=("receita", "sum"), pedidos=("produto", "size")
    ).reset_index()
    by_day["ordem"] = pd.to_datetime(by_day["dia"], format="%d/%m/%Y")
    by_day = by_day.sort_values("ordem", ascending=False).head(7)
    top_share = float(by_client.iloc[0] / revenue * 100) if revenue else None
    latest = by_month.iloc[-1]
    previous = by_month.iloc[-2] if len(by_month) > 1 else None
    delta = float((latest["receita"] / previous["receita"] - 1) * 100) if previous is not None and previous["receita"] else None
    observations = [
        {"question": "Como as vendas evoluíram?", "evidence": (
            f"A receita do último mês com registros foi R$ {latest['receita']:,.2f}; variação de {delta:+.1f}% frente ao mês anterior com registros."
            if delta is not None else "Não há dois meses com receita positiva comparáveis neste arquivo."
        ), "decision": "Investigue sazonalidade e cobertura dos meses antes de alterar metas ou estoque."},
        {"question": "A operação gerou margem?", "evidence": (
            f"Resultado bruto estimado de R$ {profit:,.2f}, margem de {margin:.1f}%." if margin is not None else "Receita zero; margem percentual indefinida."
        ), "decision": "Confira se os custos unitários estão completos. Impostos, despesas e devoluções não integram esta margem."},
        {"question": "Há concentração de clientes?", "evidence": (
            f"O maior cliente representa {top_share:.1f}% da receita registrada." if top_share is not None else "Sem receita para medir concentração."
        ), "decision": "Avalie a recorrência dos clientes e confirme se a base inclui todas as vendas do período."},
    ]
    warnings = []
    if quality["duplicates"]:
        warnings.append(f"{quality['duplicates']} linha(s) idêntica(s): confirme se são vendas repetidas; não foram removidas.")
    if quality["zero_prices"]:
        warnings.append(f"{quality['zero_prices']} linha(s) com preço zero: confirme se são brindes ou erros.")
    months = by_month.index.tolist()
    if len(months) > 1:
        all_months = pd.period_range(months[0], months[-1], freq="M").astype(str).tolist()
        absent = [m for m in all_months if m not in months]
        if absent:
            warnings.append("Meses sem registros no intervalo: " + ", ".join(absent[:12]) + ("…" if len(absent) > 12 else "") + ".")
    return {
        "kpis": {"revenue": round(revenue, 2), "cost": round(costs, 2), "profit": round(profit, 2), "margin": round(margin, 1) if margin is not None else None, "rows": quality["rows"], "clients": int(df["cliente"].nunique())},
        "months": [{"period": month, "revenue": round(float(row["receita"]), 2), "cost": round(float(row["custo"]), 2), "profit": round(float(row["resultado"]), 2)} for month, row in by_month.iterrows()],
        "categories": [{"name": name, "revenue": round(float(row["receita"]), 2), "profit": round(float(row["resultado"]), 2)} for name, row in by_category.iterrows()],
        "clients": [{"name": name, "revenue": round(float(value), 2)} for name, value in by_client.head(5).items()],
        "days": [{"date": row["dia"], "revenue": round(float(row["receita"]), 2), "records": int(row["pedidos"])} for _, row in by_day.iterrows()],
        "observations": observations, "warnings": warnings,
        "period": f"{df['data'].min():%d/%m/%Y} a {df['data'].max():%d/%m/%Y}",
    }
