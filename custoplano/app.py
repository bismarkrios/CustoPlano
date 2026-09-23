"""
Custo Plano — servidor do site e do sistema de planejamento de obras.

Rodar:
    pip install -r requirements.txt
    python app.py                          # http://localhost:5000

Usuários:
    flask --app app criar-usuario cliente@construtora.com.br
    flask --app app trocar-senha cliente@construtora.com.br
    flask --app app listar-usuarios
"""
from __future__ import annotations

import datetime as dt
import getpass
import json
import os
import secrets
from functools import wraps

import click
from flask import Flask, Response, abort, jsonify, request, send_from_directory, session

import banco
from cronograma import ErroCronograma, Projeto, ler_arquivo, nome_base

AQUI = os.path.dirname(os.path.abspath(__file__))
PASTA_WEB = os.path.join(AQUI, "web")


def chave_secreta() -> str:
    """Usa CP_SECRET do ambiente ou cria (uma vez) um arquivo com uma chave aleatória."""
    if os.environ.get("CP_SECRET"):
        return os.environ["CP_SECRET"]
    caminho = os.path.join(AQUI, ".chave_secreta")
    if not os.path.exists(caminho):
        with open(caminho, "w") as f:
            f.write(secrets.token_hex(32))
    with open(caminho) as f:
        return f.read().strip()


app = Flask(__name__, static_folder=None)
app.config.update(
    SECRET_KEY=chave_secreta(),
    MAX_CONTENT_LENGTH=80 * 1024 * 1024,  # .mpp grandes
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=os.environ.get("CP_HTTPS") == "1",
    PERMANENT_SESSION_LIFETIME=dt.timedelta(days=7),
)
DEMO = os.environ.get("CP_DEMO", "1") == "1"
DEMO_EMAIL, DEMO_SENHA = "demo@custoplano.com.br", "demo"

banco.iniciar()
if DEMO and not banco.autenticar(DEMO_EMAIL, DEMO_SENHA):
    try:
        banco.criar_usuario(DEMO_EMAIL, DEMO_SENHA, "Obra de demonstração")
    except Exception:
        banco.trocar_senha(DEMO_EMAIL, DEMO_SENHA)


# ---------------------------------------------------------------- utilidades
def logado(f):
    @wraps(f)
    def interno(*a, **k):
        if not session.get("uid"):
            return jsonify(erro="Faça login para continuar."), 401
        return f(*a, **k)
    return interno


def erro(msg: str, codigo: int = 400):
    return jsonify(erro=msg), codigo


def para_data(ms_ou_iso) -> dt.datetime:
    if isinstance(ms_ou_iso, (int, float)):
        return dt.datetime.fromtimestamp(ms_ou_iso / 1000, dt.timezone.utc).replace(tzinfo=None, hour=17, minute=0)
    return dt.datetime.strptime(str(ms_ou_iso)[:10], "%Y-%m-%d").replace(hour=17)


def para_ms(d: dt.datetime) -> int:
    return int(d.replace(tzinfo=dt.timezone.utc).timestamp() * 1000)


def carregar():
    """Cronograma do usuário logado + Projeto já interpretado."""
    c = banco.cronograma(session["uid"])
    if not c:
        return None, None
    return c, Projeto(c["xml"], c["arquivo"])


def download(conteudo, nome: str, mime: str) -> Response:
    r = Response(conteudo, mimetype=mime)
    r.headers["Content-Disposition"] = f'attachment; filename="{nome}"'
    return r


# --------------------------------------------------------------------- páginas
@app.get("/")
def inicio():
    with open(os.path.join(PASTA_WEB, "index.html"), encoding="utf-8") as f:
        html = f.read()
    # avisa o front-end de que há servidor (login real, banco e .mpp)
    html = html.replace("<!--CP_API-->", "<script>window.CP_API = true; window.CP_DEMO = %s;</script>" % ("true" if DEMO else "false"))
    return Response(html, mimetype="text/html")


@app.get("/web/<path:arquivo>")
def estaticos(arquivo):
    return send_from_directory(PASTA_WEB, arquivo)


# ------------------------------------------------------------------------ login
@app.post("/api/login")
def login():
    d = request.get_json(silent=True) or {}
    if d.get("demo"):
        if not DEMO:
            return erro("A obra de demonstração está desativada.", 403)
        email, senha = DEMO_EMAIL, DEMO_SENHA
    else:
        email, senha = (d.get("email") or "").strip(), d.get("senha") or ""
    u = banco.autenticar(email, senha)
    if not u:
        return erro("E-mail ou senha incorretos.", 401)
    session.clear()
    session.permanent = True
    session["uid"] = u["id"]
    return jsonify(email=u["email"], nome=u["nome"])


@app.post("/api/logout")
def logout():
    session.clear()
    return jsonify(ok=True)


@app.get("/api/me")
def me():
    u = banco.usuario(session["uid"]) if session.get("uid") else None
    return jsonify(email=u["email"], nome=u["nome"]) if u else jsonify(email=None)


# ------------------------------------------------------------------- cronograma
@app.get("/api/cronograma")
@logado
def ver_cronograma():
    c = banco.cronograma(session["uid"])
    if not c:
        return jsonify(xml=None)
    return jsonify(xml=c["xml"], fileName=c["arquivo"], weight=c["peso"],
                   statusDate=para_ms(para_data(c["data_status"])),
                   meas=json.loads(c["atual"]), base=json.loads(c["anterior"]))


@app.post("/api/importar")
@logado
def importar():
    arq = request.files.get("arquivo")
    if not arq or not arq.filename:
        return erro("Escolha o arquivo do cronograma (.mpp ou .xml).")
    try:
        xml = ler_arquivo(arq.filename, arq.read())
        p = Projeto(xml, arq.filename)
    except ErroCronograma as e:
        return erro(str(e))
    hoje = dt.datetime.now().replace(hour=17, minute=0, second=0, microsecond=0)
    sd = p.data_status or hoje
    banco.salvar_cronograma(session["uid"], arq.filename, xml, p.peso, sd.strftime("%Y-%m-%d"), p.medicao_arquivo)
    return ver_cronograma()


@app.post("/api/medicao")
@logado
def medicao():
    d = request.get_json(silent=True) or {}
    if not banco.cronograma(session["uid"]):
        return erro("Importe um cronograma primeiro.", 404)
    atual = {str(k): max(0.0, min(100.0, float(v))) for k, v in (d.get("meas") or {}).items()}
    anterior = d.get("base")
    anterior = {str(k): float(v) for k, v in anterior.items()} if isinstance(anterior, dict) else None
    sd = para_data(d.get("statusDate") or dt.date.today().isoformat())
    banco.salvar_medicao(session["uid"], atual, str(d.get("weight") or "dur"), sd.strftime("%Y-%m-%d"), anterior)
    return jsonify(ok=True)


@app.post("/api/fechar")
@logado
def fechar():
    c, p = carregar()
    if not c:
        return erro("Importe um cronograma primeiro.", 404)
    sd = para_data(c["data_status"])
    A = p.agregar(json.loads(c["atual"]), json.loads(c["anterior"]), sd, c["peso"])
    banco.fechar_medicao(session["uid"], p.pct(A["total"], "r"), p.pct(A["total"], "p"))
    return jsonify(ok=True)


@app.post("/api/remover")
@logado
def remover():
    banco.remover_cronograma(session["uid"])
    return jsonify(ok=True)


@app.get("/api/historico")
@logado
def ver_historico():
    return jsonify([dict(h) for h in banco.historico(session["uid"])])


# -------------------------------------------------------------------- exportar
def _exportar(c, p, formato: str, sufixo: str):
    atual, anterior = json.loads(c["atual"]), json.loads(c["anterior"])
    sd = para_data(c["data_status"])
    base = f"{nome_base(c['arquivo'])}_{sufixo}_{sd:%Y-%m-%d}"
    if formato == "xml":
        return download(p.exportar_xml(atual, sd), base + ".xml", "application/xml")
    if formato == "csv":
        return download(p.exportar_csv(atual, anterior, sd, c["peso"]), base + ".csv", "text/csv; charset=utf-8")
    if formato == "xlsx":
        return download(p.exportar_xlsx(atual, anterior, sd, c["peso"]), base + ".xlsx",
                        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    abort(404)


@app.get("/api/exportar/<formato>")
@logado
def exportar(formato):
    c, p = carregar()
    if not c:
        return erro("Importe um cronograma primeiro.", 404)
    return _exportar(c, p, formato, "medicao" if formato == "xml" else "boletim")


@app.get("/api/historico/<int:mid>/<formato>")
@logado
def exportar_historico(mid, formato):
    c = banco.medicao_fechada(session["uid"], mid)
    if not c:
        abort(404)
    return _exportar(c, Projeto(c["xml"], c["arquivo"]), formato, "medicao" if formato == "xml" else "boletim")


# ------------------------------------------------------------ comandos (CLI)
@app.cli.command("criar-usuario")
@click.argument("email")
@click.option("--nome", default="", help="Nome do cliente ou da obra")
def cmd_criar_usuario(email, nome):
    """Cria o acesso de um cliente."""
    senha = getpass.getpass("Senha: ")
    if len(senha) < 6:
        raise click.ClickException("Use uma senha com pelo menos 6 caracteres.")
    banco.criar_usuario(email, senha, nome)
    click.echo(f"Usuário {email} criado.")


@app.cli.command("trocar-senha")
@click.argument("email")
def cmd_trocar_senha(email):
    """Troca a senha de um usuário."""
    senha = getpass.getpass("Nova senha: ")
    click.echo("Senha alterada." if banco.trocar_senha(email, senha) else "Usuário não encontrado.")


@app.cli.command("listar-usuarios")
def cmd_listar():
    """Lista os usuários cadastrados."""
    for u in banco.listar_usuarios():
        click.echo(f"{u['id']:>4}  {u['email']:<40} {u['nome']}")


if __name__ == "__main__":
    app.run(host=os.environ.get("CP_HOST", "127.0.0.1"), port=int(os.environ.get("PORT", 5000)), debug=False)
