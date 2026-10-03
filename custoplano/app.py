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
from plano_ataque import ErroPlano, ler_plano, plano_do_projeto
import custos as cst
import qualidade as ql

AQUI = os.path.dirname(os.path.abspath(__file__))
PASTA_WEB = os.path.join(AQUI, "web")


def chave_secreta() -> str:
    """Usa CP_SECRET do ambiente ou cria (uma vez) um arquivo com uma chave aleatória."""
    if os.environ.get("CP_SECRET"):
        return os.environ["CP_SECRET"]
    # fica junto do banco (no disco permanente, quando houver), para os logins sobreviverem a reinícios
    caminho = os.path.join(os.path.dirname(banco.CAMINHO), ".chave_secreta")
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
    # no Render (que define RENDER) o site sempre roda em HTTPS
    SESSION_COOKIE_SECURE=os.environ.get("CP_HTTPS") == "1" or bool(os.environ.get("RENDER")),
    PERMANENT_SESSION_LIFETIME=dt.timedelta(days=7),
)
DEMO = os.environ.get("CP_DEMO", "1") == "1"
DEMO_EMAIL, DEMO_SENHA = "demo@custoplano.com.br", "demo"

banco.iniciar()
ql.iniciar()
# Primeiro acesso do administrador: defina CP_ADMIN_EMAIL e CP_ADMIN_SENHA no painel do Render.
# A conta só é criada se ainda não existir; depois a senha não é mais tocada pela variável.
_adm_email, _adm_senha = os.environ.get("CP_ADMIN_EMAIL", "").strip().lower(), os.environ.get("CP_ADMIN_SENHA", "")
if _adm_email and len(_adm_senha) < 8:
    print("CP_ADMIN_SENHA precisa de pelo menos 8 caracteres; conta de acesso não criada.", flush=True)
elif _adm_email:
    _adm = next((u for u in banco.listar_usuarios() if u["email"].lower() == _adm_email), None)
    if _adm is None:
        banco.criar_usuario(_adm_email, _adm_senha, "Custo Plano")
        print(f"Conta de acesso criada: {_adm_email}", flush=True)
    elif not banco.autenticar(_adm["email"], _adm_senha):
        banco.trocar_senha(_adm["email"], _adm_senha)
        print(f"Senha da conta {_adm_email} atualizada pela CP_ADMIN_SENHA.", flush=True)
if DEMO and not banco.autenticar(DEMO_EMAIL, DEMO_SENHA):
    try:
        banco.criar_usuario(DEMO_EMAIL, DEMO_SENHA, "Obra de demonstração")
    except Exception:
        banco.trocar_senha(DEMO_EMAIL, DEMO_SENHA)


# ------------------------------------------------------------------ segurança
@app.after_request
def cabecalhos_seguranca(r: Response) -> Response:
    """Cabeçalhos que impedem o site de ser embutido em outra página e reduzem ataques comuns."""
    r.headers.setdefault("X-Frame-Options", "DENY")
    r.headers.setdefault("X-Content-Type-Options", "nosniff")
    r.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    r.headers.setdefault("Permissions-Policy", "geolocation=(), microphone=()")
    if request.is_secure or request.headers.get("X-Forwarded-Proto") == "https":
        r.headers.setdefault("Strict-Transport-Security", "max-age=31536000")
    return r


TENTATIVAS: dict[str, list[float]] = {}
MAX_TENTATIVAS, JANELA = 5, 15 * 60  # 5 erros de senha em 15 minutos bloqueiam o e-mail naquele IP


def _ip() -> str:
    return (request.headers.get("X-Forwarded-For") or request.remote_addr or "").split(",")[0].strip()


def _bloqueado(chave: str) -> bool:
    agora_s = dt.datetime.now().timestamp()
    TENTATIVAS[chave] = [t for t in TENTATIVAS.get(chave, []) if agora_s - t < JANELA]
    return len(TENTATIVAS[chave]) >= MAX_TENTATIVAS


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
    r = Response(html, mimetype="text/html")
    r.headers["Cache-Control"] = "no-cache"  # sempre confere se há versão nova do site
    return r


@app.get("/web/<path:arquivo>")
def estaticos(arquivo):
    return send_from_directory(PASTA_WEB, arquivo)


@app.get("/sw.js")
def service_worker():
    """Service worker na raiz, para o app funcionar sem internet em todo o site."""
    r = send_from_directory(PASTA_WEB, "sw.js", mimetype="application/javascript")
    r.headers["Cache-Control"] = "no-cache"
    r.headers["Service-Worker-Allowed"] = "/"
    return r


@app.get("/manifest.webmanifest")
def manifesto():
    return send_from_directory(PASTA_WEB, "manifest.webmanifest", mimetype="application/manifest+json")


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
    chave = f"{_ip()}|{email.lower()}"
    if _bloqueado(chave):
        return erro("Muitas tentativas de login. Aguarde 15 minutos e tente de novo.", 429)
    u = banco.autenticar(email, senha)
    if not u:
        TENTATIVAS.setdefault(chave, []).append(dt.datetime.now().timestamp())
        return erro("E-mail ou senha incorretos.", 401)
    TENTATIVAS.pop(chave, None)
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


@app.post("/api/importar-exemplo")
@logado
def importar_exemplo():
    """Carrega o cronograma de exemplo para testar a medição sem ter um .mpp à mão."""
    nome = "cronograma_exemplo.xml"
    with open(os.path.join(AQUI, "exemplo", nome), encoding="utf-8") as f:
        xml = f.read()
    p = Projeto(xml, nome)
    sd = p.data_status or dt.datetime.now().replace(hour=17, minute=0, second=0, microsecond=0)
    banco.salvar_cronograma(session["uid"], nome, xml, p.peso, sd.strftime("%Y-%m-%d"), p.medicao_arquivo)
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


# -------------------------------------------------- plano de ataque (escadinha)
def plano_de(uid: int):
    """Plano de ataque montado a partir do cronograma importado; a planilha só entra se o
    cronograma não tiver serviços por pavimento."""
    c = banco.cronograma(uid)
    if c:
        d = plano_do_projeto(Projeto(c["xml"], c["arquivo"]), json.loads(c["atual"]), para_data(c["data_status"]))
        if d:
            return d, c["atualizado_em"] if "atualizado_em" in c.keys() else None
    pl = banco.plano(uid)
    return (json.loads(pl["dados"]), pl["atualizado_em"]) if pl else (None, None)


@app.get("/api/plano")
@logado
def ver_plano():
    d, quando = plano_de(session["uid"])
    return jsonify(plano=d, atualizado_em=quando)


@app.post("/api/plano/importar")
@logado
def importar_plano():
    arq = request.files.get("arquivo")
    if not arq or not arq.filename:
        return erro("Escolha a planilha do plano de ataque (.xlsx).")
    try:
        dados = ler_plano(arq.filename, arq.read())
    except ErroPlano as e:
        return erro(str(e))
    banco.salvar_plano(session["uid"], arq.filename, dados)
    return ver_plano()


@app.post("/api/plano/remover")
@logado
def remover_plano():
    banco.remover_plano(session["uid"])
    return jsonify(ok=True)


# ------------------------------------------------------------------- custos
def orcamento_de(uid: int, p=None):
    """Orçamento em uso: o das colunas de custo do cronograma importado; a planilha só entra
    se o cronograma não tiver custos."""
    reg = banco.custos(uid)
    dados = json.loads(reg["dados"]) if reg else None
    if p is not None and (dados is None or dados.get("origem") == "project"):
        dp = cst.dados_do_projeto(p)
        if dp or (dados and dados.get("origem") == "project"):
            dados = dp
    return reg, dados


def analise_custos(uid: int):
    c = banco.cronograma(uid)
    p = Projeto(c["xml"], c["arquivo"]) if c else None
    reg, dados = orcamento_de(uid, p)
    if not dados:
        return None
    atual = json.loads(c["atual"]) if c else {}
    ds = para_data(c["data_status"]).date() if c else dt.date.today()
    a = cst.analisar(dados, json.loads(reg["lancamentos"]) if reg else [], p, atual, ds)
    a["atualizado_em"] = reg["atualizado_em"] if reg else None
    a["origem"] = dados.get("origem") or "planilha"
    return a


@app.get("/api/custos")
@logado
def ver_custos():
    return jsonify(custos=analise_custos(session["uid"]))


@app.post("/api/custos/importar")
@logado
def importar_custos():
    arq = request.files.get("arquivo")
    if not arq or not arq.filename:
        return erro("Escolha a planilha do orçamento (.xlsx).")
    try:
        dados = cst.ler_custos(arq.filename, arq.read())
    except cst.ErroCustos as e:
        return erro(str(e))
    banco.salvar_orcamento(session["uid"], arq.filename, dados)
    return ver_custos()


@app.post("/api/custos/exemplo")
@logado
def exemplo_custos():
    uid = session["uid"]
    if not banco.cronograma(uid):
        importar_exemplo()
    c, p = carregar()
    dados = cst.ler_custos("orcamento_exemplo.xlsx", cst.exemplo(p, json.loads(c["atual"])))
    banco.salvar_orcamento(uid, "orcamento_exemplo.xlsx", dados)
    return ver_custos()


@app.get("/api/custos/modelo.xlsx")
def modelo_custos():
    return download(cst.modelo(), "modelo_orcamento_custo_plano.xlsx",
                    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


@app.get("/api/custos/financeiro.xlsx")
@logado
def exportar_financeiro():
    a = analise_custos(session["uid"])
    if not a:
        return erro("Importe o orçamento primeiro.", 404)
    return download(cst.exportar_xlsx(a), "cronograma_financeiro.xlsx",
                    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


@app.post("/api/custos/lancamento")
@logado
def lancar_custo():
    reg = banco.custos(session["uid"])
    if not reg:
        _, p = carregar()
        _, dados = orcamento_de(session["uid"], p)
        if not dados:
            return erro("Importe o orçamento antes de lançar custos.", 404)
        banco.salvar_orcamento(session["uid"], dados.get("arquivo") or "cronograma", dados)
        reg = banco.custos(session["uid"])
    d = request.get_json(silent=True) or {}
    try:
        data_l = dt.date.fromisoformat(str(d.get("data"))[:10])
        valor = cst.numero(d.get("valor"))
        if valor is None:
            raise ValueError
    except (TypeError, ValueError):
        return erro("Informe a data e o valor do lançamento.")
    if valor == 0:
        return erro("Informe um valor diferente de zero.")
    lista = json.loads(reg["lancamentos"])
    lista.append({"id": secrets.token_hex(6), "data": data_l.isoformat(), "codigo": str(d.get("codigo") or "").strip(),
                  "descricao": str(d.get("descricao") or "").strip()[:200], "fornecedor": str(d.get("fornecedor") or "").strip()[:120],
                  "tipo": cst.tipo_de(d.get("tipo")), "valor": round(valor, 2), "origem": "sistema",
                  "criado_em": dt.datetime.now().isoformat(timespec="seconds")})
    banco.salvar_lancamentos(session["uid"], lista)
    return ver_custos()


@app.post("/api/custos/lancamento/remover")
@logado
def remover_lancamento():
    reg = banco.custos(session["uid"])
    if not reg:
        return erro("Nada para remover.", 404)
    lid = (request.get_json(silent=True) or {}).get("id")
    banco.salvar_lancamentos(session["uid"], [x for x in json.loads(reg["lancamentos"]) if x.get("id") != lid])
    return ver_custos()


@app.post("/api/custos/remover")
@logado
def remover_custos():
    banco.remover_custos(session["uid"])
    return jsonify(ok=True)


# ---------------------------------------------------------------- qualidade
def _ql(fn):
    try:
        fn()
    except ql.ErroQualidade as e:
        return erro(str(e))
    return jsonify(ql.estado(session["uid"]))


@app.get("/api/qualidade")
@logado
def ver_qualidade():
    return jsonify(ql.estado(session["uid"]))


@app.post("/api/qualidade/modelo")
@logado
def qualidade_modelo():
    return _ql(lambda: ql.salvar_modelo(session["uid"], request.get_json(silent=True) or {}))


@app.post("/api/qualidade/modelo/remover")
@logado
def qualidade_modelo_remover():
    return _ql(lambda: ql.remover_modelo(session["uid"], int((request.get_json(silent=True) or {}).get("id") or 0)))


@app.post("/api/qualidade/inspecao")
@logado
def qualidade_inspecao():
    return _ql(lambda: ql.registrar_inspecao(session["uid"], request.get_json(silent=True) or {}))


@app.post("/api/qualidade/inspecao/remover")
@logado
def qualidade_inspecao_remover():
    return _ql(lambda: ql.remover_inspecao(session["uid"], int((request.get_json(silent=True) or {}).get("id") or 0)))


@app.post("/api/qualidade/nc")
@logado
def qualidade_nc():
    return _ql(lambda: ql.salvar_nc(session["uid"], request.get_json(silent=True) or {}))


@app.get("/api/qualidade/foto/<fid>")
@logado
def qualidade_foto(fid):
    f = ql.foto(session["uid"], fid)
    if not f:
        abort(404)
    r = Response(f["dados"], mimetype=f["mime"])
    r.headers["Cache-Control"] = "private, max-age=31536000, immutable"
    return r


@app.get("/api/qualidade/inspecao/<int:iid>.pdf")
@logado
def qualidade_inspecao_pdf(iid):
    import relatorio

    i = ql.inspecao(session["uid"], iid)
    if not i:
        abort(404)
    u = banco.usuario(session["uid"])
    c, p = carregar()
    obra = (p.titulo if p else "") or (u["nome"] if u else "") or "Obra"
    pdf = relatorio.fvs_pdf(i, lambda fid: ql.foto(session["uid"], fid), obra)
    return download(pdf, f"{i['codigo']}_{nome_base(i['local'])}_{i['data']}.pdf", "application/pdf")


# ----------------------------------------------------------- relatório mensal
@app.get("/api/relatorio.pdf")
@logado
def relatorio_pdf():
    import relatorio

    c, p = carregar()
    plano, _ = plano_de(session["uid"])
    cus = analise_custos(session["uid"])
    if not c and not plano and not cus:
        return erro("Importe o cronograma (tela Project), o plano de ataque ou o orçamento para gerar o relatório.", 404)
    u = banco.usuario(session["uid"])
    obra = (p.titulo if p else (plano or {}).get("titulo")) or (u["nome"] if u else "") or "Obra"
    hist = [dict(h) for h in banco.historico(session["uid"])]
    if c:
        sd = para_data(c["data_status"])
        pdf = relatorio.gerar(p, json.loads(c["atual"]), json.loads(c["anterior"]), sd, c["peso"], hist, plano, obra, cus)
    else:
        sd = dt.datetime.now()
        pdf = relatorio.gerar(None, {}, {}, sd, None, [], plano, obra, cus)
    return download(pdf, f"{nome_base(obra)}_relatorio_{sd:%Y-%m}.pdf", "application/pdf")


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
