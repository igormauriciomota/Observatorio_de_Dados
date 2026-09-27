import hashlib
from io import BytesIO
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from flask import Flask, redirect, render_template, request, send_file, session, url_for
import pandas as pd
from werkzeug.exceptions import RequestEntityTooLarge

from .analysis import analyze, load_sales
from .finance import FIELDS, market_indicators, statement_indicators


def create_app(test_config=None):
    app = Flask(__name__, instance_relative_config=True)
    app.config.update(
        SECRET_KEY="dev-only-change-this-before-deployment",
        MAX_CONTENT_LENGTH=5 * 1024 * 1024,
        UPLOAD_DIR=Path(app.instance_path) / "uploads",
    )
    if test_config:
        app.config.update(test_config)
    app.config["UPLOAD_DIR"] = Path(app.config["UPLOAD_DIR"])
    app.config["UPLOAD_DIR"].mkdir(parents=True, exist_ok=True)

    @app.get("/")
    def index():
        return render_template("index.html")

    @app.post("/importar")
    def upload():
        file = request.files.get("arquivo")
        if not file or not file.filename:
            return render_template("index.html", error="Escolha uma planilha CSV ou XLSX."), 400
        extension = Path(file.filename).suffix.lower()
        if extension not in {".csv", ".xlsx"}:
            return render_template("index.html", error="Formato aceito: CSV ou XLSX."), 400
        token = uuid4().hex
        path = app.config["UPLOAD_DIR"] / f"{token}{extension}"
        file.save(path)
        try:
            # Valide antes de registrar a planilha na sessão.
            load_sales(path)
        except Exception as exc:
            path.unlink(missing_ok=True)
            # Erros de parser não devem expor caminhos locais ou detalhes internos.
            message = str(exc) if isinstance(exc, ValueError) else "Não foi possível ler a planilha. Confira o formato e os dados."
            return render_template("index.html", error=message), 400
        session["dataset"] = token
        session["extension"] = extension
        session["filename"] = Path(file.filename).name[:120]
        session["imported_at"] = datetime.now(timezone.utc).strftime("%d/%m/%Y %H:%M UTC")
        return redirect(url_for("dashboard"))

    @app.get("/painel")
    def dashboard():
        token = session.get("dataset", "")
        extension = session.get("extension", "")
        if not isinstance(token, str) or len(token) != 32 or any(c not in "0123456789abcdef" for c in token) or extension not in {".csv", ".xlsx"}:
            return redirect(url_for("index"))
        path = app.config["UPLOAD_DIR"] / f"{token}{extension}"
        if not path.is_file():
            session.clear()
            return redirect(url_for("index"))
        try:
            original, quality = load_sales(path)
            month_options = sorted(original["data"].dt.to_period("M").astype(str).unique())
            category_options = sorted(original["categoria"].unique())
            chosen_month = request.args.get("mes", "")
            chosen_category = request.args.get("categoria", "")
            if chosen_month not in month_options:
                chosen_month = ""
            if chosen_category not in category_options:
                chosen_category = ""
            frame = original
            if chosen_month:
                frame = frame.loc[frame["data"].dt.to_period("M").astype(str).eq(chosen_month)]
            if chosen_category:
                frame = frame.loc[frame["categoria"].eq(chosen_category)]
            filter_warning = "Essa combinação não possui registros; exibindo a base completa." if frame.empty else None
            if frame.empty:
                frame = original
                chosen_month = chosen_category = ""
            selected_quality = {"rows": len(frame), "duplicates": int(frame.duplicated(subset=["data", "cliente", "produto", "categoria", "quantidade", "preco_unitario", "custo_unitario"]).sum()), "zero_prices": int(frame["preco_unitario"].eq(0).sum())}
            result = analyze(frame, selected_quality)
        except (ValueError, OSError):
            session.clear()
            return render_template("index.html", error="A planilha não está mais disponível. Importe-a novamente."), 400
        result["source"] = {
            "filename": session.get("filename", "Planilha"),
            "imported_at": session.get("imported_at", ""),
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest()[:16],
        }
        return render_template("dashboard.html", result=result, month_options=month_options, category_options=category_options, chosen_month=chosen_month, chosen_category=chosen_category, filter_warning=filter_warning)

    @app.get("/modelo")
    def sample():
        return send_file(Path(app.root_path).parent / "data" / "exemplo_vendas.csv", as_attachment=True)

    @app.get("/exportar/<format_name>")
    def export(format_name):
        if format_name not in {"csv", "xlsx"}:
            return "Formato indisponível", 404
        token = session.get("dataset", "")
        extension = session.get("extension", "")
        if not isinstance(token, str) or len(token) != 32 or any(c not in "0123456789abcdef" for c in token) or extension not in {".csv", ".xlsx"}:
            return redirect(url_for("index"))
        path = app.config["UPLOAD_DIR"] / f"{token}{extension}"
        if not path.is_file():
            return redirect(url_for("index"))
        frame, quality = load_sales(path)
        month = request.args.get("mes", "")
        category = request.args.get("categoria", "")
        if month in frame["data"].dt.to_period("M").astype(str).values:
            frame = frame.loc[frame["data"].dt.to_period("M").astype(str).eq(month)]
        if category in frame["categoria"].values:
            frame = frame.loc[frame["categoria"].eq(category)]
        if frame.empty:
            return redirect(url_for("dashboard"))
        quality = {"rows": len(frame), "duplicates": 0, "zero_prices": int(frame["preco_unitario"].eq(0).sum())}
        result = analyze(frame, quality)
        monthly = pd.DataFrame(result["months"]).rename(columns={"period": "mes", "revenue": "receita", "cost": "custo", "profit": "resultado_bruto"})
        if format_name == "csv":
            contents = monthly.to_csv(index=False, sep=";", decimal=",").encode("utf-8-sig")
            return send_file(BytesIO(contents), mimetype="text/csv", as_attachment=True, download_name="resumo_mensal.csv")
        buffer = BytesIO()
        def excel_safe(value):
            return "'" + value if isinstance(value, str) and value.startswith(("=", "+", "-", "@")) else value
        categories = pd.DataFrame(result["categories"]).rename(columns={"name": "categoria", "revenue": "receita", "profit": "resultado_bruto"})
        clients = pd.DataFrame(result["clients"]).rename(columns={"name": "cliente", "revenue": "receita"})
        categories["categoria"] = categories["categoria"].map(excel_safe)
        clients["cliente"] = clients["cliente"].map(excel_safe)
        with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
            monthly.to_excel(writer, sheet_name="Mensal", index=False)
            categories.to_excel(writer, sheet_name="Categorias", index=False)
            clients.to_excel(writer, sheet_name="Clientes", index=False)
            pd.DataFrame([{"origem": excel_safe(session.get("filename", "")), "linhas": quality["rows"], "duplicadas": quality["duplicates"], "preco_zero": quality["zero_prices"]}]).to_excel(writer, sheet_name="Origem e qualidade", index=False)
        buffer.seek(0)
        return send_file(buffer, mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", as_attachment=True, download_name="analise_vendas.xlsx")

    @app.errorhandler(RequestEntityTooLarge)
    def too_large(error):
        return render_template("index.html", error="O arquivo deve ter no máximo 5 MB."), 413

    @app.route("/laboratorio", methods=["GET", "POST"])
    def laboratory():
        indicators = market = error = None
        if request.method == "POST":
            try:
                if request.form.get("analysis_type") == "statement":
                    indicators = statement_indicators(request.form)
                elif request.form.get("analysis_type") == "market":
                    market = market_indicators(request.files.get("arquivo_precos"), request.form.get("posicao", ""))
                else:
                    raise ValueError("Escolha uma análise válida.")
            except (ValueError, AttributeError) as exc:
                error = str(exc)
        return render_template("laboratory.html", fields=FIELDS, indicators=indicators, market=market, error=error)

    @app.get("/modelo-precos")
    def market_sample():
        return send_file(Path(app.root_path).parent / "data" / "exemplo_precos.csv", as_attachment=True)

    return app
