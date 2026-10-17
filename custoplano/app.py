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
import io
import json
import os
import re
import secrets
import shutil
import threading
from functools import wraps

import click
from flask import Flask, Response, abort, jsonify, request, send_from_directory, session

import banco
import correio
from cronograma import ErroCronograma, Projeto, enxugar_xml, ler_arquivo, nome_base
from plano_ataque import ErroPlano, ler_plano, plano_do_projeto
import custos as cst
import demo
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

# Cronogramas gravados antes do XML enxuto: enxuga uma vez, um por vez (ver cronograma.enxugar_xml).
for _uid in banco.cronogramas_grandes():
    try:
        _c = banco.cronograma(_uid)
        _antes = len(_c["xml"])
        _xml = enxugar_xml(io.BytesIO(_c["xml"].encode("utf-8")))
        del _c
        banco.trocar_xml(_uid, _xml)
        print(f"Cronograma do usuário {_uid} enxugado: {_antes // 1024} KB -> {len(_xml) // 1024} KB", flush=True)
        del _xml
    except Exception as _e:  # noqa: BLE001 - nunca impedir o site de subir por causa disso
        print(f"Não consegui enxugar o cronograma do usuário {_uid}: {_e}", flush=True)
ql.iniciar()
# Primeiro acesso do administrador: defina CP_ADMIN_EMAIL e CP_ADMIN_SENHA no painel do Render.
# A conta só é criada se ainda não existir; depois a senha não é mais tocada pela variável.
_adm_email, _adm_senha = os.environ.get("CP_ADMIN_EMAIL", "").strip().lower(), os.environ.get("CP_ADMIN_SENHA", "")
if _adm_email and len(_adm_senha) < 8:
    print("CP_ADMIN_SENHA precisa de pelo menos 8 caracteres; conta de acesso não criada.", flush=True)
elif _adm_email:
    _adm = next((u for u in banco.listar_usuarios() if u["email"].lower() == _adm_email), None)
    if _adm is None:
        banco.criar_usuario(_adm_email, _adm_senha, "Custo Plano", admin=True)
        print(f"Conta de acesso criada: {_adm_email}", flush=True)
    else:
        banco.tornar_admin(_adm["id"])  # a conta da CP_ADMIN_EMAIL é da equipe: vê todas as obras
        if not banco.autenticar(_adm["email"], _adm_senha):
            banco.trocar_senha(_adm["email"], _adm_senha)
            print(f"Senha da conta {_adm_email} atualizada pela CP_ADMIN_SENHA.", flush=True)
if DEMO and not banco.autenticar(DEMO_EMAIL, DEMO_SENHA):
    try:
        banco.criar_usuario(DEMO_EMAIL, DEMO_SENHA, "Obra de demonstração")
    except Exception:
        banco.trocar_senha(DEMO_EMAIL, DEMO_SENHA)
if DEMO:
    _du = banco.usuario_por_email(DEMO_EMAIL)
    if _du and not banco.obras_do_usuario(_du["id"]):
        banco.definir_acesso(_du["id"], banco.criar_obra("Obra de demonstração", "Custo Plano"), "editor")

# A demonstração usa as mesmas telas da área do cliente, com uma obra de exemplo gravada na conta demo.
# Ela é refeita no login da demo (no máximo a cada 15 minutos), com as datas em torno de hoje.
_DEMO_SEMEADA = [0.0]


def semear_demo(forcar: bool = False) -> None:
    agora_s = dt.datetime.now().timestamp()
    if not forcar and agora_s - _DEMO_SEMEADA[0] < 15 * 60:
        return
    u = banco.autenticar(DEMO_EMAIL, DEMO_SENHA)
    obras = banco.obras_do_usuario(u["id"]) if u else []
    if not obras:
        return
    try:
        with _PRJ_TRAVA:
            _PRJ_CACHE.clear()
            demo.semear(obras[0]["id"])
        _DEMO_SEMEADA[0] = agora_s
    except Exception as e:  # noqa: BLE001 - a demo nunca pode derrubar o site
        print(f"Não consegui montar a obra de demonstração: {e}", flush=True)


# Cópia de segurança diária do banco inteiro (todas as obras, com fotos) na pasta copias do disco
# permanente; ficam as 7 últimas. Para guardar fora do servidor: Administração > Baixar cópia.
def _copias_diarias() -> None:
    import time

    while True:
        try:
            hoje = dt.date.today().isoformat()
            if not any(c["nome"].startswith("custoplano-" + hoje) for c in banco.listar_copias()):
                livre = shutil.disk_usage(os.path.dirname(os.path.abspath(banco.CAMINHO))).free
                if livre < 2 * os.path.getsize(banco.CAMINHO) + 50 * 1024 * 1024:
                    print("ATENÇÃO: pouco espaço no disco; a cópia de segurança de hoje não foi feita.", flush=True)
                else:
                    print(f"Cópia de segurança feita: {os.path.basename(banco.copia_de_seguranca())}", flush=True)
                    banco.limpar_copias(7)
        except Exception as e:  # noqa: BLE001 - nunca derrubar o site por causa da cópia
            print(f"Não consegui fazer a cópia de segurança: {e}", flush=True)
        time.sleep(3600)


if os.environ.get("CP_COPIAS_AUTO", "1") == "1":
    threading.Thread(target=_copias_diarias, name="copias", daemon=True).start()


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
MAX_POR_EMAIL = 20  # e 20 erros no mesmo e-mail, vindos de qualquer IP, bloqueiam o e-mail por 15 minutos


def _ip() -> str:
    # O primeiro IP do X-Forwarded-For é escrito por quem faz o pedido e pode ser inventado; o último
    # é o que o proxy do Render viu de fato.
    xff = [x.strip() for x in (request.headers.get("X-Forwarded-For") or "").split(",") if x.strip()]
    return xff[-1] if xff else (request.remote_addr or "")


def _bloqueado(chave: str, limite: int = MAX_TENTATIVAS) -> bool:
    agora_s = dt.datetime.now().timestamp()
    if len(TENTATIVAS) > 20000:  # não deixa a lista crescer sem fim
        for k in [k for k, v in TENTATIVAS.items() if not v or agora_s - v[-1] >= JANELA]:
            TENTATIVAS.pop(k, None)
    TENTATIVAS[chave] = [t for t in TENTATIVAS.get(chave, []) if agora_s - t < JANELA]
    return len(TENTATIVAS[chave]) >= limite


# ---------------------------------------------------------------- utilidades
def sessao_valida() -> bool:
    """Login vale enquanto a senha for a mesma: trocar a senha derruba as sessões abertas."""
    uid = session.get("uid")
    if not uid:
        return False
    if session.get("pv") != banco.versao_senha(uid):
        session.clear()
        return False
    return True


def oid() -> int:
    """Obra aberta nesta sessão (todos os dados do sistema são da obra, não do login)."""
    return session["obra"]


def logado(f):
    """Rotas de dados: precisa de login, de uma obra aberta a que o usuário ainda tenha acesso e,
    para gravar, de acesso de edição (quem tem acesso de leitura só consulta)."""
    @wraps(f)
    def interno(*a, **k):
        if not sessao_valida():
            return jsonify(erro="Faça login para continuar."), 401
        papel = banco.papel_na_obra(session["uid"], _obra_da_sessao()) if _obra_da_sessao() else None
        if not papel:
            return jsonify(erro="Escolha uma obra para continuar.", sem_obra=True), 409
        if papel == "leitor" and request.method not in ("GET", "HEAD"):
            return erro("Seu acesso a esta obra é só para consulta. Peça acesso de edição à Custo Plano.", 403)
        return f(*a, **k)
    return interno


def _obra_da_sessao() -> int | None:
    """Sessões abertas antes das obras (ou sem obra escolhida) abrem a última obra usada ou a primeira liberada."""
    if not session.get("obra"):
        u = banco.usuario(session["uid"])
        ids = [o["id"] for o in banco.obras_do_usuario(session["uid"])]
        escolha = u["obra_atual"] if u and u["obra_atual"] in ids else (ids[0] if ids else None)
        if escolha:
            session["obra"] = escolha
    return session.get("obra")


def so_logado(f):
    """Rotas da conta (trocar senha, escolher obra): só precisam de login."""
    @wraps(f)
    def interno(*a, **k):
        if not sessao_valida():
            return jsonify(erro="Faça login para continuar."), 401
        return f(*a, **k)
    return interno


def so_admin(f):
    """Administração (obras, usuários, cópias): só a equipe Custo Plano."""
    @wraps(f)
    def interno(*a, **k):
        if not sessao_valida():
            return jsonify(erro="Faça login para continuar."), 401
        u = banco.usuario(session["uid"])
        if not u or not u["admin"]:
            return erro("Só a equipe Custo Plano acessa a administração.", 403)
        return f(*a, **k)
    return interno


def ehdemo() -> bool:
    return bool(session.get("demo"))


def sem_demo(f):
    """Importar arquivos e apagar dados ficam desligados na demonstração, que é aberta a qualquer visitante."""
    @wraps(f)
    def interno(*a, **k):
        if ehdemo():
            return erro("Na demonstração, importar arquivos e apagar dados fica desligado. Na sua conta funciona normalmente.", 403)
        return f(*a, **k)
    return interno


def erro(msg: str, codigo: int = 400):
    return jsonify(erro=msg), codigo


@app.errorhandler(ValueError)
@app.errorhandler(TypeError)
def dados_invalidos(_e):
    """Número, data ou id em formato errado vindo do navegador: responde 400, nunca 500."""
    return erro("Dados inválidos.")


def para_data(ms_ou_iso) -> dt.datetime:
    if isinstance(ms_ou_iso, (int, float)):
        return dt.datetime.fromtimestamp(ms_ou_iso / 1000, dt.timezone.utc).replace(tzinfo=None, hour=17, minute=0)
    return dt.datetime.strptime(str(ms_ou_iso)[:10], "%Y-%m-%d").replace(hour=17)


def para_ms(d: dt.datetime) -> int:
    return int(d.replace(tzinfo=dt.timezone.utc).timestamp() * 1000)


# Interpretar um cronograma grande ocupa muita memória (o servidor tem 512 MB): um de cada vez,
# e o último fica guardado para as telas que pedem o mesmo cronograma em seguida.
_PRJ_TRAVA = threading.Lock()
_PRJ_CACHE: dict = {}


def projeto_de(c, fresco: bool = False) -> Projeto:
    chave = (c["usuario_id"], c["arquivo"], hash(c["xml"]))
    with _PRJ_TRAVA:
        if not fresco and _PRJ_CACHE.get("chave") == chave:
            return _PRJ_CACHE["p"]
        _PRJ_CACHE.clear()
        p = Projeto(c["xml"], c["arquivo"])
        if not fresco:  # exportar altera a árvore do XML: esse não vai para o cache
            _PRJ_CACHE.update(chave=chave, p=p)
        return p


def carregar(fresco: bool = False):
    """Cronograma do usuário logado + Projeto já interpretado."""
    c = banco.cronograma(oid())
    if not c:
        return None, None
    return c, projeto_de(c, fresco)


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
    # Só rodam os scripts do próprio site (com o nonce desta resposta): um texto malicioso que
    # chegue à página por um arquivo importado não consegue executar código.
    nonce = secrets.token_urlsafe(16)
    html = html.replace("<script>", f'<script nonce="{nonce}">')
    r = Response(html, mimetype="text/html")
    r.headers["Cache-Control"] = "no-cache"  # sempre confere se há versão nova do site
    r.headers["Content-Security-Policy"] = (
        f"default-src 'self'; script-src 'self' 'nonce-{nonce}'; "
        "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; font-src 'self' https://fonts.gstatic.com; "
        "img-src 'self' data: blob:; connect-src 'self'; worker-src 'self'; manifest-src 'self'; "
        "object-src 'none'; base-uri 'self'; form-action 'self'; frame-ancestors 'none'")
    return r


@app.get("/manual")
def pagina_manual():
    """Página de venda do ebook (os anúncios e o site apontam para cá)."""
    r = send_from_directory(PASTA_WEB, "manual.html", mimetype="text/html")
    r.headers["Cache-Control"] = "no-cache"
    return r


@app.get("/privacidade")
def pagina_privacidade():
    """Política de privacidade e termos de uso (LGPD)."""
    r = send_from_directory(PASTA_WEB, "privacidade.html", mimetype="text/html")
    r.headers["Cache-Control"] = "no-cache"
    return r


@app.get("/robots.txt")
def robots():
    """Libera o site para o Google e esconde a área do sistema."""
    texto = "User-agent: *\nAllow: /\nDisallow: /api/\n\nSitemap: https://custoplano.com.br/sitemap.xml\n"
    return Response(texto, mimetype="text/plain")


@app.get("/sitemap.xml")
def sitemap():
    """Páginas públicas para o Google Search Console."""
    urls = "".join(f"<url><loc>https://custoplano.com.br{p}</loc></url>" for p in ("/", "/manual", "/privacidade"))
    xml = ('<?xml version="1.0" encoding="UTF-8"?>'
           f'<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{urls}</urlset>')
    return Response(xml, mimetype="application/xml")


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
        semear_demo()
    else:
        email, senha = (d.get("email") or "").strip(), d.get("senha") or ""
    chave, chave_email = f"{_ip()}|{email.lower()}", f"*|{email.lower()}"
    if _bloqueado(chave) or _bloqueado(chave_email, MAX_POR_EMAIL):
        return erro("Muitas tentativas de login. Aguarde 15 minutos e tente de novo.", 429)
    u = banco.autenticar(email, senha)
    if not u:
        agora_s = dt.datetime.now().timestamp()
        TENTATIVAS.setdefault(chave, []).append(agora_s)
        TENTATIVAS.setdefault(chave_email, []).append(agora_s)
        return erro("E-mail ou senha incorretos.", 401)
    TENTATIVAS.pop(chave, None)
    abrir_sessao(u["id"])
    return jsonify(conta())


def abrir_sessao(uid: int) -> None:
    """Login: abre a última obra usada (ou a primeira liberada)."""
    u = banco.usuario(uid)
    session.clear()
    session.permanent = True
    session["uid"] = uid
    session["pv"] = banco.versao_senha(uid)
    if u["email"].lower() == DEMO_EMAIL:
        session["demo"] = True
    ids = [o["id"] for o in banco.obras_do_usuario(uid)]
    obra_id = u["obra_atual"] if u["obra_atual"] in ids else (ids[0] if ids else None)
    if obra_id:
        session["obra"] = obra_id
    banco.registrar_entrada(uid, obra_id)


def conta() -> dict:
    """O que o navegador precisa saber do login: quem é, se é da equipe e quais obras pode abrir."""
    u = banco.usuario(session["uid"])
    obras = banco.obras_do_usuario(u["id"])
    atual = next((o for o in obras if o["id"] == _obra_da_sessao()), None)
    return dict(email=u["email"], nome=u["nome"], admin=bool(u["admin"]), obra=atual, obras=obras,
                senha_email=bool(smtp_configurado()))


@app.post("/api/logout")
def logout():
    session.clear()
    return jsonify(ok=True)


@app.get("/api/me")
def me():
    return jsonify(conta()) if sessao_valida() else jsonify(email=None, senha_email=bool(smtp_configurado()))


@app.post("/api/obra/abrir")
@so_logado
def abrir_obra():
    obra_id = int((request.get_json(silent=True) or {}).get("id") or 0)
    if not banco.papel_na_obra(session["uid"], obra_id):
        return erro("Você não tem acesso a essa obra.", 403)
    session["obra"] = obra_id
    banco.registrar_entrada(session["uid"], obra_id)
    return jsonify(conta())


# ---------------------------------------------------------------------- senhas
SENHA_MIN = 8


def _senha_ok(senha: str) -> str | None:
    if len(senha) < SENHA_MIN:
        return f"Use uma senha com pelo menos {SENHA_MIN} caracteres."
    if len(senha) > 200:
        return "Senha longa demais."
    return None


@app.post("/api/senha/link")
def verificar_link_senha():
    """A tela de criar senha confere o link antes de mostrar o formulário."""
    r = banco.link_senha(str((request.get_json(silent=True) or {}).get("codigo") or ""))
    if not r:
        return erro("Este link já foi usado ou venceu. Peça um link novo à Custo Plano.", 400)
    return jsonify(email=r["email"])


@app.post("/api/senha/definir")
def definir_senha():
    d = request.get_json(silent=True) or {}
    senha = str(d.get("senha") or "")
    if (m := _senha_ok(senha)):
        return erro(m)
    chave = f"link|{_ip()}"
    if _bloqueado(chave, 20):
        return erro("Muitas tentativas. Aguarde 15 minutos e tente de novo.", 429)
    uid = banco.usar_link_senha(str(d.get("codigo") or ""), senha)
    if not uid:
        TENTATIVAS.setdefault(chave, []).append(dt.datetime.now().timestamp())
        return erro("Este link já foi usado ou venceu. Peça um link novo à Custo Plano.", 400)
    abrir_sessao(uid)
    return jsonify(conta())


@app.post("/api/senha/trocar")
@so_logado
def trocar_minha_senha():
    if ehdemo():
        return erro("A senha da demonstração não pode ser trocada.", 403)
    d = request.get_json(silent=True) or {}
    u = banco.usuario(session["uid"])
    if not banco.autenticar(u["email"], str(d.get("atual") or "")):
        return erro("A senha atual não confere.", 400)
    nova = str(d.get("nova") or "")
    if (m := _senha_ok(nova)):
        return erro(m)
    banco.trocar_senha_id(u["id"], nova)
    session["pv"] = banco.versao_senha(u["id"])  # esta sessão continua; as outras caem
    return jsonify(ok=True)


def smtp_configurado() -> bool:
    return correio.configurado() is not None


def endereco_site() -> str:
    return os.environ.get("CP_SITE", "").rstrip("/") or request.host_url.rstrip("/")


@app.post("/api/senha/esqueci")
def esqueci_senha():
    """Manda um link de troca de senha (vale 2 horas). A resposta é a mesma, exista ou não o e-mail."""
    email = str((request.get_json(silent=True) or {}).get("email") or "").strip()
    if not smtp_configurado():
        return erro("Para trocar a senha, peça um link à Custo Plano pelo WhatsApp.", 503)
    k_ip, k_email = f"esq|{_ip()}", f"esq|*|{email.lower()}"
    if _bloqueado(k_ip) or _bloqueado(k_email, 3):
        return erro("Muitos pedidos. Aguarde 15 minutos e tente de novo.", 429)
    agora_s = dt.datetime.now().timestamp()
    TENTATIVAS.setdefault(k_ip, []).append(agora_s)
    TENTATIVAS.setdefault(k_email, []).append(agora_s)
    u = banco.usuario_por_email(email) if email else None
    if u and u["email"].lower() != DEMO_EMAIL:
        codigo = banco.criar_link_senha(u["id"], 2)
        texto = (f"Olá{(' ' + u['nome']) if u['nome'] else ''},\n\n"
                 "Para criar uma senha nova, abra o link abaixo (vale por 2 horas e só funciona uma vez):\n\n"
                 f"{endereco_site()}/#senha={codigo}\n\n"
                 "Se você não pediu a troca, ignore este e-mail: sua senha atual continua valendo.\n\nCusto Plano")
        try:
            correio.enviar(u["email"], "Custo Plano: troca de senha", texto)
        except Exception as e:  # noqa: BLE001
            print(f"Não consegui enviar o e-mail de troca de senha: {e}", flush=True)
            return erro("Não consegui enviar o e-mail agora. Peça um link à Custo Plano pelo WhatsApp.", 502)
    return jsonify(ok=True, msg="Se este e-mail tiver acesso, você vai receber um link para criar a senha nova.")


# ------------------------------------------------------------- administração
def _email_valido(e: str) -> bool:
    return bool(re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", e)) and len(e) <= 200


@app.get("/api/admin")
@so_admin
def admin_painel():
    d = banco.painel_admin()
    d["copias"] = banco.listar_copias()[:10]
    d["eu"] = session["uid"]
    return jsonify(d)


@app.post("/api/admin/obra")
@so_admin
def admin_obra():
    d = request.get_json(silent=True) or {}
    nome, empresa = str(d.get("nome") or "").strip()[:120], str(d.get("empresa") or "").strip()[:120]
    if not nome:
        return erro("Informe o nome da obra.")
    if d.get("id"):
        if not banco.obra(int(d["id"])):
            return erro("Obra não encontrada.", 404)
        banco.editar_obra(int(d["id"]), nome, empresa)
        return jsonify(id=int(d["id"]))
    return jsonify(id=banco.criar_obra(nome, empresa))


@app.post("/api/admin/obra/remover")
@so_admin
def admin_obra_remover():
    d = request.get_json(silent=True) or {}
    o = banco.obra(int(d.get("id") or 0))
    if not o:
        return erro("Obra não encontrada.", 404)
    if str(d.get("confirmar") or "").strip().lower() != o["nome"].strip().lower():
        return erro("Para apagar, digite o nome da obra exatamente como aparece.")
    banco.copia_de_seguranca(f"antes-de-apagar-obra-{o['id']}")
    banco.remover_obra(o["id"])
    if session.get("obra") == o["id"]:
        session.pop("obra", None)
    return jsonify(ok=True)


@app.post("/api/admin/usuario")
@so_admin
def admin_usuario():
    """Cria o acesso de uma pessoa e devolve o link para ela criar a própria senha (vale 7 dias)."""
    d = request.get_json(silent=True) or {}
    email, nome = str(d.get("email") or "").strip().lower(), str(d.get("nome") or "").strip()[:120]
    if not _email_valido(email):
        return erro("Informe um e-mail válido.")
    if banco.usuario_por_email(email):
        return erro("Já existe um acesso com esse e-mail.")
    uid = banco.criar_usuario(email, None, nome)
    if d.get("equipe") is True:  # equipe Custo Plano: todas as obras e a Administração
        banco.tornar_admin(uid)
    for a in d.get("acessos") or []:
        if banco.obra(int(a.get("obra_id") or 0)) and a.get("papel") in ("editor", "leitor"):
            banco.definir_acesso(uid, int(a["obra_id"]), a["papel"])
    return jsonify(id=uid, link=f"{endereco_site()}/#senha={banco.criar_link_senha(uid, 24 * 7)}")


@app.post("/api/admin/acesso")
@so_admin
def admin_acesso():
    d = request.get_json(silent=True) or {}
    uid, obra_id, papel = int(d.get("usuario_id") or 0), int(d.get("obra_id") or 0), d.get("papel")
    if not banco.usuario(uid) or not banco.obra(obra_id):
        return erro("Usuário ou obra não encontrados.", 404)
    if papel not in ("editor", "leitor", None):
        return erro("Papel inválido.")
    banco.definir_acesso(uid, obra_id, papel)
    return jsonify(ok=True)


@app.post("/api/admin/link-senha")
@so_admin
def admin_link_senha():
    """Link novo para a pessoa criar ou trocar a senha (vale 7 dias), para mandar pelo WhatsApp."""
    u = banco.usuario(int((request.get_json(silent=True) or {}).get("usuario_id") or 0))
    if not u or u["email"].lower() == DEMO_EMAIL:
        return erro("Usuário não encontrado.", 404)
    return jsonify(link=f"{endereco_site()}/#senha={banco.criar_link_senha(u['id'], 24 * 7)}", email=u["email"])


@app.post("/api/admin/usuario/remover")
@so_admin
def admin_usuario_remover():
    u = banco.usuario(int((request.get_json(silent=True) or {}).get("id") or 0))
    if not u:
        return erro("Usuário não encontrado.", 404)
    if u["id"] == session["uid"] or u["email"].lower() == DEMO_EMAIL or u["admin"]:
        return erro("Esse acesso não pode ser removido por aqui.", 403)
    banco.remover_usuario(u["id"])
    return jsonify(ok=True)


@app.get("/api/admin/copia")
@so_admin
def admin_baixar_copia():
    """Cópia completa e atual do banco (todas as obras, com fotos), para guardar fora do servidor."""
    caminho = banco.copia_de_seguranca("baixada")
    r = send_from_directory(banco.pasta_copias(), os.path.basename(caminho), as_attachment=True, mimetype="application/gzip")
    r.headers["Cache-Control"] = "no-store"
    return r


# ----------------------------------------------------------------------- saúde
@app.get("/saude")
def saude():
    """Para o monitor externo e o Render: 200 só se o banco está respondendo."""
    try:
        with banco.conectar() as con:
            con.execute("SELECT 1 FROM usuarios LIMIT 1").fetchall()
        return jsonify(ok=True)
    except Exception:  # noqa: BLE001
        return jsonify(ok=False), 503


# ------------------------------------------------------------------- cronograma
@app.get("/api/cronograma")
@logado
def ver_cronograma():
    c = banco.cronograma(oid())
    if not c:
        return jsonify(xml=None)
    return jsonify(xml=c["xml"], fileName=c["arquivo"], weight=c["peso"],
                   statusDate=para_ms(para_data(c["data_status"])),
                   meas=json.loads(c["atual"]), base=json.loads(c["anterior"]))


@app.post("/api/importar")
@logado
@sem_demo
def importar():
    arq = request.files.get("arquivo")
    if not arq or not arq.filename:
        return erro("Escolha o arquivo do cronograma (.mpp ou .xml).")
    try:
        with _PRJ_TRAVA:
            _PRJ_CACHE.clear()
            xml = ler_arquivo(arq.filename, arq.read())
            p = Projeto(xml, arq.filename)
            if any(not re.fullmatch(r"\d{1,9}", t.uid or "") for t in p.tarefas):
                raise ErroCronograma("O cronograma tem tarefas com identificador (UID) inválido. Salve de novo pelo MS Project e envie outra vez.")
        print(f"Cronograma importado: {arq.filename}, {len(p.tarefas)} tarefas, XML de {len(xml) // 1024} KB", flush=True)
    except ErroCronograma as e:
        return erro(str(e))
    hoje = dt.datetime.now().replace(hour=17, minute=0, second=0, microsecond=0)
    sd = p.data_status or hoje
    banco.salvar_cronograma(oid(), arq.filename, xml, p.peso, sd.strftime("%Y-%m-%d"), p.medicao_arquivo)
    return ver_cronograma()


@app.post("/api/importar-exemplo")
@logado
@sem_demo
def importar_exemplo():
    """Carrega o cronograma de exemplo para testar a medição sem ter um .mpp à mão."""
    nome = "cronograma_exemplo.xml"
    with open(os.path.join(AQUI, "exemplo", nome), encoding="utf-8") as f:
        xml = f.read()
    p = Projeto(xml, nome)
    sd = p.data_status or dt.datetime.now().replace(hour=17, minute=0, second=0, microsecond=0)
    banco.salvar_cronograma(oid(), nome, xml, p.peso, sd.strftime("%Y-%m-%d"), p.medicao_arquivo)
    return ver_cronograma()


@app.post("/api/medicao")
@logado
def medicao():
    d = request.get_json(silent=True) or {}
    if not banco.cronograma(oid()):
        return erro("Importe um cronograma primeiro.", 404)
    atual = {str(k): max(0.0, min(100.0, float(v))) for k, v in (d.get("meas") or {}).items()}
    anterior = d.get("base")
    anterior = {str(k): float(v) for k, v in anterior.items()} if isinstance(anterior, dict) else None
    sd = para_data(d.get("statusDate") or dt.date.today().isoformat())
    banco.salvar_medicao(oid(), atual, str(d.get("weight") or "dur"), sd.strftime("%Y-%m-%d"), anterior)
    return jsonify(ok=True)


@app.post("/api/fechar")
@logado
def fechar():
    c, p = carregar()
    if not c:
        return erro("Importe um cronograma primeiro.", 404)
    sd = para_data(c["data_status"])
    A = p.agregar(json.loads(c["atual"]), json.loads(c["anterior"]), sd, c["peso"])
    banco.fechar_medicao(oid(), p.pct(A["total"], "r"), p.pct(A["total"], "p"))
    return jsonify(ok=True)


@app.post("/api/remover")
@logado
@sem_demo
def remover():
    banco.remover_cronograma(oid())
    return jsonify(ok=True)


@app.get("/api/historico")
@logado
def ver_historico():
    return jsonify([dict(h) for h in banco.historico(oid())])


# -------------------------------------------------- plano de ataque (escadinha)
def plano_de(uid: int):
    """Plano de ataque montado a partir do cronograma importado; a planilha só entra se o
    cronograma não tiver serviços por pavimento."""
    c = banco.cronograma(uid)
    if c:
        d = plano_do_projeto(projeto_de(c), json.loads(c["atual"]), para_data(c["data_status"]))
        if d:
            return d, c["atualizado_em"] if "atualizado_em" in c.keys() else None
    pl = banco.plano(uid)
    return (json.loads(pl["dados"]), pl["atualizado_em"]) if pl else (None, None)


@app.get("/api/plano")
@logado
def ver_plano():
    d, quando = plano_de(oid())
    return jsonify(plano=d, atualizado_em=quando)


@app.post("/api/plano/importar")
@logado
@sem_demo
def importar_plano():
    arq = request.files.get("arquivo")
    if not arq or not arq.filename:
        return erro("Escolha a planilha do plano de ataque (.xlsx).")
    try:
        dados = ler_plano(arq.filename, arq.read())
    except ErroPlano as e:
        return erro(str(e))
    banco.salvar_plano(oid(), arq.filename, dados)
    return ver_plano()


@app.post("/api/plano/remover")
@logado
@sem_demo
def remover_plano():
    banco.remover_plano(oid())
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
    p = projeto_de(c) if c else None
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
    return jsonify(custos=analise_custos(oid()))


@app.post("/api/custos/importar")
@logado
@sem_demo
def importar_custos():
    arq = request.files.get("arquivo")
    if not arq or not arq.filename:
        return erro("Escolha a planilha do orçamento (.xlsx).")
    try:
        dados = cst.ler_custos(arq.filename, arq.read())
    except cst.ErroCustos as e:
        return erro(str(e))
    banco.salvar_orcamento(oid(), arq.filename, dados)
    return ver_custos()


@app.post("/api/custos/exemplo")
@logado
@sem_demo
def exemplo_custos():
    uid = oid()
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
    a = analise_custos(oid())
    if not a:
        return erro("Importe o orçamento primeiro.", 404)
    return download(cst.exportar_xlsx(a), "cronograma_financeiro.xlsx",
                    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


@app.post("/api/custos/lancamento")
@logado
def lancar_custo():
    reg = banco.custos(oid())
    if not reg:
        _, p = carregar()
        _, dados = orcamento_de(oid(), p)
        if not dados:
            return erro("Importe o orçamento antes de lançar custos.", 404)
        banco.salvar_orcamento(oid(), dados.get("arquivo") or "cronograma", dados)
        reg = banco.custos(oid())
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
    banco.salvar_lancamentos(oid(), lista)
    return ver_custos()


@app.post("/api/custos/lancamento/remover")
@logado
def remover_lancamento():
    reg = banco.custos(oid())
    if not reg:
        return erro("Nada para remover.", 404)
    lid = (request.get_json(silent=True) or {}).get("id")
    banco.salvar_lancamentos(oid(), [x for x in json.loads(reg["lancamentos"]) if x.get("id") != lid])
    return ver_custos()


@app.post("/api/custos/remover")
@logado
@sem_demo
def remover_custos():
    banco.remover_custos(oid())
    return jsonify(ok=True)


# ------------------------------------------------------------- suprimentos
SUP_ETAPAS = ("solicitar", "cotacao", "pedido", "entregue")


@app.get("/api/suprimentos")
@logado
def ver_suprimentos():
    return jsonify(itens=banco.suprimentos(oid()))


@app.post("/api/suprimentos")
@logado
def salvar_suprimento():
    """Grava prazo do fornecedor, fornecedor e etapa da compra de uma linha resumo do cronograma."""
    d = request.get_json(silent=True) or {}
    uid = str(d.get("uid") or "")
    if not re.fullmatch(r"\d{1,9}", uid):
        return erro("Linha do cronograma inválida.")
    itens = banco.suprimentos(oid())
    it = dict(itens.get(uid) or {})
    if "lead" in d:
        it["lead"] = max(0, min(365, int(d["lead"])))
    if "forn" in d:
        it["forn"] = str(d["forn"] or "").strip()[:120]
    if "st" in d:
        if d["st"] not in SUP_ETAPAS:
            return erro("Etapa inválida.")
        it["st"] = d["st"]
    itens[uid] = it
    if len(itens) > 5000:
        return erro("Limite de itens de suprimentos atingido.")
    banco.salvar_suprimentos(oid(), itens)
    return jsonify(itens=itens)


# --------------------------------------------------------------- curto prazo
CP_CAUSAS = ("Material", "Mão de obra", "Projeto", "Equipamento", "Clima", "Predecessora", "Qualidade")


@app.get("/api/curtoprazo")
@logado
def ver_curto_prazo():
    return jsonify(semanas=banco.curto_prazo(oid()))


@app.post("/api/curtoprazo")
@logado
def salvar_curto_prazo():
    """Grava o apontamento de um pacote na semana: concluído, não concluído (com a causa) ou sem apontamento."""
    d = request.get_json(silent=True) or {}
    sem, uid = str(d.get("semana") or ""), str(d.get("uid") or "")
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", sem) or not re.fullmatch(r"\d{1,9}", uid):
        return erro("Semana ou tarefa inválida.")
    st, causa = d.get("st") or "", d.get("causa") or ""
    if st not in ("", "ok", "nok") or (causa and causa not in CP_CAUSAS):
        return erro("Apontamento inválido.")
    dados = banco.curto_prazo(oid())
    semana = dados.setdefault(sem, {})
    if st:
        semana[uid] = {"st": st, "causa": causa if st == "nok" else ""}
    else:
        semana.pop(uid, None)
    if not semana:
        dados.pop(sem, None)
    if len(dados) > 520 or sum(len(v) for v in dados.values()) > 50000:
        return erro("Limite de apontamentos atingido.")
    banco.salvar_curto_prazo(oid(), dados)
    return jsonify(semanas=dados)


# ---------------------------------------------------------------- qualidade
def _ql(fn):
    if ehdemo() and "data:image" in (request.get_data(as_text=True) or ""):
        return erro("Na demonstração, o envio de fotos fica desligado. Na sua conta funciona normalmente.", 403)
    try:
        fn()
    except ql.ErroQualidade as e:
        return erro(str(e))
    return jsonify(ql.estado(oid()))


@app.get("/api/qualidade")
@logado
def ver_qualidade():
    return jsonify(ql.estado(oid()))


@app.post("/api/qualidade/modelo")
@logado
def qualidade_modelo():
    return _ql(lambda: ql.salvar_modelo(oid(), request.get_json(silent=True) or {}))


@app.post("/api/qualidade/modelo/remover")
@logado
@sem_demo
def qualidade_modelo_remover():
    return _ql(lambda: ql.remover_modelo(oid(), int((request.get_json(silent=True) or {}).get("id") or 0)))


@app.post("/api/qualidade/inspecao")
@logado
def qualidade_inspecao():
    return _ql(lambda: ql.registrar_inspecao(oid(), request.get_json(silent=True) or {}))


@app.post("/api/qualidade/inspecao/remover")
@logado
@sem_demo
def qualidade_inspecao_remover():
    return _ql(lambda: ql.remover_inspecao(oid(), int((request.get_json(silent=True) or {}).get("id") or 0)))


@app.post("/api/qualidade/nc")
@logado
def qualidade_nc():
    return _ql(lambda: ql.salvar_nc(oid(), request.get_json(silent=True) or {}))


@app.get("/api/qualidade/foto/<fid>")
@logado
def qualidade_foto(fid):
    f = ql.foto(oid(), fid)
    if not f:
        abort(404)
    r = Response(f["dados"], mimetype=f["mime"])
    r.headers["Cache-Control"] = "private, max-age=31536000, immutable"
    return r


@app.get("/api/qualidade/inspecao/<int:iid>.pdf")
@logado
def qualidade_inspecao_pdf(iid):
    import relatorio

    i = ql.inspecao(oid(), iid)
    if not i:
        abort(404)
    o = banco.obra(oid())
    c, p = carregar()
    obra = (p.titulo if p else "") or (o["nome"] if o else "") or "Obra"
    pdf = relatorio.fvs_pdf(i, lambda fid: ql.foto(oid(), fid), obra)
    return download(pdf, f"{i['codigo']}_{nome_base(i['local'])}_{i['data']}.pdf", "application/pdf")


# ----------------------------------------------------------- relatório mensal
@app.get("/api/relatorio.pdf")
@logado
def relatorio_pdf():
    import relatorio

    c, p = carregar()
    plano, _ = plano_de(oid())
    cus = analise_custos(oid())
    if not c and not plano and not cus:
        return erro("Importe o cronograma (tela Project), o plano de ataque ou o orçamento para gerar o relatório.", 404)
    o = banco.obra(oid())
    obra = (p.titulo if p else (plano or {}).get("titulo")) or (o["nome"] if o else "") or "Obra"
    hist = [dict(h) for h in banco.historico(oid())]
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
        forn = p.fornecedores({k: v.get("forn", "") for k, v in banco.suprimentos(oid()).items()})
        return download(p.exportar_xml(atual, sd, forn), base + ".xml", "application/xml")
    if formato == "csv":
        return download(p.exportar_csv(atual, anterior, sd, c["peso"]), base + ".csv", "text/csv; charset=utf-8")
    if formato == "xlsx":
        return download(p.exportar_xlsx(atual, anterior, sd, c["peso"]), base + ".xlsx",
                        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    abort(404)


@app.get("/api/exportar/<formato>")
@logado
def exportar(formato):
    c, p = carregar(fresco=True)
    if not c:
        return erro("Importe um cronograma primeiro.", 404)
    return _exportar(c, p, formato, "medicao" if formato == "xml" else "boletim")


@app.get("/api/historico/<int:mid>/<formato>")
@logado
def exportar_historico(mid, formato):
    c = banco.medicao_fechada(oid(), mid)
    if not c:
        abort(404)
    return _exportar(c, projeto_de(c, fresco=True), formato, "medicao" if formato == "xml" else "boletim")


# ------------------------------------------------------------ comandos (CLI)
@app.cli.command("criar-usuario")
@click.argument("email")
@click.option("--nome", default="", help="Nome da pessoa")
@click.option("--admin", is_flag=True, help="Equipe Custo Plano (vê todas as obras)")
def cmd_criar_usuario(email, nome, admin):
    """Cria um acesso (as obras de cada pessoa são liberadas na tela Administração)."""
    senha = getpass.getpass("Senha: ")
    if len(senha) < 8:
        raise click.ClickException("Use uma senha com pelo menos 8 caracteres.")
    banco.criar_usuario(email, senha, nome, admin=admin)
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
