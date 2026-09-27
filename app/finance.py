"""Indicadores exploratórios para demonstrações e uma série histórica de preços."""

from io import BytesIO

import numpy as np
import pandas as pd


FIELDS = {
    "lucro_liquido": "Lucro líquido",
    "patrimonio_liquido": "Patrimônio líquido",
    "capital_investido": "Capital investido",
    "lucro_operacional": "Lucro operacional (EBIT)",
    "depreciacao_amortizacao": "Depreciação e amortização",
    "divida_bruta": "Dívida bruta",
    "caixa": "Caixa e equivalentes",
    "ativo_circulante": "Ativo circulante",
    "passivo_circulante": "Passivo circulante",
}


def statement_indicators(raw):
    values = {}
    for name, label in FIELDS.items():
        text = str(raw.get(name, "")).strip().replace("R$", "").replace(" ", "")
        if "," in text:
            text = text.replace(".", "").replace(",", ".")
        try:
            values[name] = float(text)
            if not np.isfinite(values[name]):
                raise ValueError
        except ValueError as exc:
            raise ValueError(f"Informe um valor numérico válido para {label}.") from exc
    if values["patrimonio_liquido"] <= 0 or values["capital_investido"] <= 0 or values["passivo_circulante"] <= 0:
        raise ValueError("Patrimônio líquido, capital investido e passivo circulante devem ser maiores que zero.")
    ebitda = values["lucro_operacional"] + values["depreciacao_amortizacao"]
    net_debt = values["divida_bruta"] - values["caixa"]
    return {
        "roe": round(values["lucro_liquido"] / values["patrimonio_liquido"] * 100, 2),
        "roi": round(values["lucro_liquido"] / values["capital_investido"] * 100, 2),
        "ebitda": round(ebitda, 2),
        "net_debt": round(net_debt, 2),
        "debt_ebitda": round(net_debt / ebitda, 2) if ebitda > 0 else None,
        "liquidity": round(values["ativo_circulante"] / values["passivo_circulante"], 2),
    }


def market_indicators(uploaded, position):
    name = (uploaded.filename or "").lower()
    if not name.endswith((".csv", ".xlsx")):
        raise ValueError("Envie uma série de preços em CSV ou XLSX.")
    raw = uploaded.read()
    if len(raw) > 5 * 1024 * 1024:
        raise ValueError("Arquivo acima de 5 MB.")
    try:
        if name.endswith(".xlsx"):
            frame = pd.read_excel(BytesIO(raw), engine="openpyxl")
        else:
            try:
                frame = pd.read_csv(BytesIO(raw), sep=None, engine="python", encoding="utf-8-sig")
            except UnicodeDecodeError:
                frame = pd.read_csv(BytesIO(raw), sep=None, engine="python", encoding="cp1252")
    except Exception as exc:
        raise ValueError("Não foi possível ler o arquivo de preços.") from exc
    frame.columns = [str(c).strip().lower() for c in frame.columns]
    if not {"data", "fechamento"}.issubset(frame.columns):
        raise ValueError("A série precisa das colunas data e fechamento. Volume é opcional.")
    if not 100 <= len(frame) <= 10000:
        raise ValueError("Informe entre 100 e 10.000 pregões para comparar o modelo e calcular risco.")
    frame["data"] = pd.to_datetime(frame["data"], format="%Y-%m-%d", errors="coerce")
    frame["fechamento"] = pd.to_numeric(frame["fechamento"], errors="coerce")
    if frame["data"].isna().any() or frame["fechamento"].isna().any() or (frame["fechamento"] <= 0).any():
        raise ValueError("Datas devem estar em AAAA-MM-DD; fechamentos devem ser números positivos com ponto decimal.")
    if frame["data"].duplicated().any():
        raise ValueError("Há datas repetidas. Mantenha um fechamento por pregão.")
    if "volume" in frame:
        frame["volume"] = pd.to_numeric(frame["volume"], errors="coerce")
        if frame["volume"].isna().any() or (frame["volume"] < 0).any():
            raise ValueError("Volume deve conter números não negativos em todas as linhas.")
    frame = frame.sort_values("data").reset_index(drop=True)
    try:
        position = float(str(position).replace(",", "."))
        if not np.isfinite(position) or position <= 0:
            raise ValueError
    except ValueError as exc:
        raise ValueError("Informe o valor positivo da posição em reais.") from exc
    close = frame["fechamento"]
    returns = close.pct_change().dropna()
    var95 = max(0.0, -float(np.quantile(returns, 0.05)) * position)
    sma20 = close.rolling(20).mean()
    sma50 = close.rolling(50).mean()

    # Previsão experimental: regressão linear treinada com dados anteriores à validação.
    # Cada linha usa apenas fechamentos já conhecidos para prever o próximo pregão.
    features, targets = [], []
    for i in range(50, len(close)):
        history = close.iloc[:i]
        features.append([1.0, float(history.iloc[-1]), float(history.iloc[-5:].mean()), float(history.iloc[-20:].mean())])
        targets.append(float(close.iloc[i]))
    x, y = np.asarray(features), np.asarray(targets)
    split = max(1, int(len(y) * .8))
    coef = np.linalg.lstsq(x[:split], y[:split], rcond=None)[0]
    predicted = x[split:] @ coef
    mae_model = float(np.mean(np.abs(predicted - y[split:])))
    mae_naive = float(np.mean(np.abs(x[split:, 1] - y[split:])))
    next_features = np.array([1.0, float(close.iloc[-1]), float(close.iloc[-5:].mean()), float(close.iloc[-20:].mean())])
    forecast = float(next_features @ coef)
    return {
        "rows": len(frame), "period": f"{frame['data'].iloc[0]:%d/%m/%Y} a {frame['data'].iloc[-1]:%d/%m/%Y}",
        "last": round(float(close.iloc[-1]), 2), "sma20": round(float(sma20.iloc[-1]), 2), "sma50": round(float(sma50.iloc[-1]), 2),
        "support20": round(float(close.iloc[-20:].min()), 2), "resistance20": round(float(close.iloc[-20:].max()), 2),
        "var95": round(var95, 2), "position": round(position, 2), "stress": round(position * .2, 2),
        "forecast": round(forecast, 2), "mae_model": round(mae_model, 2), "mae_naive": round(mae_naive, 2),
        "model_better": mae_model < mae_naive, "test_count": len(y[split:]),
        "volume20": round(float(frame["volume"].tail(20).mean())) if "volume" in frame else None,
        "chart": [{"date": f"{row['data']:%d/%m}", "close": round(float(row["fechamento"]), 2), "sma20": round(float(sma20.iloc[i]), 2) if i >= 19 else None, "sma50": round(float(sma50.iloc[i]), 2) if i >= 49 else None, "volume": int(row["volume"]) if "volume" in frame else None} for i, row in frame.tail(90).iterrows()],
    }
