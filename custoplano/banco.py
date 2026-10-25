"""Banco de dados SQLite do Custo Plano: usuários, cronogramas, medições e histórico."""
from __future__ import annotations

import datetime as dt
import gzip
import hashlib
import json
import os
import re
import secrets
import shutil
import sqlite3

from werkzeug.security import check_password_hash, generate_password_hash

CAMINHO = os.environ.get("CP_BANCO", os.path.join(os.path.dirname(os.path.abspath(__file__)), "custoplano.db"))

ESQUEMA = """
CREATE TABLE IF NOT EXISTS usuarios (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    email       TEXT NOT NULL UNIQUE COLLATE NOCASE,
    nome        TEXT NOT NULL DEFAULT '',
    senha_hash  TEXT NOT NULL,
    criado_em   TEXT NOT NULL,
    admin       INTEGER NOT NULL DEFAULT 0,    -- equipe Custo Plano: vê todas as obras e cadastra clientes
    obra_atual  INTEGER,                       -- última obra aberta (volta nela no próximo login)
    ultimo_login TEXT,
    whatsapp    TEXT                           -- celular para pedir o relatório pelo WhatsApp (só dígitos, com 55)
);
-- Cada obra guarda os próprios dados; nas tabelas de dados, a coluna usuario_id é o id da obra
-- (o nome ficou de quando cada login tinha uma obra só).
CREATE TABLE IF NOT EXISTS obras (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    nome        TEXT NOT NULL,
    empresa     TEXT NOT NULL DEFAULT '',
    criado_em   TEXT NOT NULL,
    servicos    TEXT NOT NULL DEFAULT 'planejamento,custos,qualidade'   -- serviços contratados, separados por vírgula
);
CREATE TABLE IF NOT EXISTS acessos (
    usuario_id  INTEGER NOT NULL REFERENCES usuarios(id) ON DELETE CASCADE,
    obra_id     INTEGER NOT NULL REFERENCES obras(id) ON DELETE CASCADE,
    papel       TEXT NOT NULL DEFAULT 'editor',   -- editor | leitor
    PRIMARY KEY (usuario_id, obra_id)
);
CREATE TABLE IF NOT EXISTS links_senha (
    hash        TEXT PRIMARY KEY,                 -- sha256 do código do link (o código em si não fica guardado)
    usuario_id  INTEGER NOT NULL REFERENCES usuarios(id) ON DELETE CASCADE,
    expira_em   TEXT NOT NULL,
    usado_em    TEXT,
    criado_em   TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS cronogramas (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    usuario_id   INTEGER NOT NULL UNIQUE REFERENCES obras(id) ON DELETE CASCADE,
    arquivo      TEXT NOT NULL,
    xml          TEXT NOT NULL,
    peso         TEXT NOT NULL DEFAULT 'dur',
    data_status  TEXT NOT NULL,
    atual        TEXT NOT NULL DEFAULT '{}',   -- JSON {uid: %}
    anterior     TEXT NOT NULL DEFAULT '{}',   -- JSON {uid: %}
    atualizado_em TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS medicoes_fechadas (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    usuario_id    INTEGER NOT NULL REFERENCES obras(id) ON DELETE CASCADE,
    arquivo       TEXT NOT NULL,
    data_status   TEXT NOT NULL,
    fechado_em    TEXT NOT NULL,
    avanco_real   REAL NOT NULL,
    avanco_prev   REAL NOT NULL,
    peso          TEXT NOT NULL,
    xml           TEXT NOT NULL,
    atual         TEXT NOT NULL,
    anterior      TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS custos (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    usuario_id    INTEGER NOT NULL UNIQUE REFERENCES obras(id) ON DELETE CASCADE,
    arquivo       TEXT NOT NULL,
    dados         TEXT NOT NULL,                -- JSON do orçamento (custos.ler_custos)
    lancamentos   TEXT NOT NULL DEFAULT '[]',   -- JSON: custos realizados lançados pelo sistema
    atualizado_em TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS planos_ataque (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    usuario_id    INTEGER NOT NULL UNIQUE REFERENCES obras(id) ON DELETE CASCADE,
    arquivo       TEXT NOT NULL,
    dados         TEXT NOT NULL,                -- JSON lido da planilha (plano_ataque.ler_plano)
    atualizado_em TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS suprimentos (
    usuario_id    INTEGER PRIMARY KEY REFERENCES obras(id) ON DELETE CASCADE,
    dados         TEXT NOT NULL,                -- JSON: {uid da linha resumo: {lead, forn, st}}
    atualizado_em TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS curto_prazo (
    usuario_id    INTEGER PRIMARY KEY REFERENCES obras(id) ON DELETE CASCADE,
    dados         TEXT NOT NULL,                -- JSON: {segunda-feira da semana: {uid da tarefa: {st, causa}}}
    atualizado_em TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS controle_custos (
    usuario_id    INTEGER PRIMARY KEY REFERENCES obras(id) ON DELETE CASCADE,
    arquivo       TEXT NOT NULL,
    dados         TEXT NOT NULL,                -- JSON: planilha Orçado x Realizado (custos.ler_controle)
    atualizado_em TEXT NOT NULL
);
"""


def conectar() -> sqlite3.Connection:
    con = sqlite3.connect(CAMINHO)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    return con


# tabelas com os dados de uma obra (a coluna usuario_id guarda o id da obra)
TABELAS_DA_OBRA = ("cronogramas", "medicoes_fechadas", "custos", "planos_ataque", "suprimentos", "curto_prazo",
                   "fvs_modelos", "inspecoes", "ncs", "fotos", "fvs_tarefas")


def iniciar() -> None:
    _colunas_novas()
    with conectar() as con:
        con.executescript(ESQUEMA)
        if "servicos" not in {r[1] for r in con.execute("PRAGMA table_info(obras)")}:
            con.execute("ALTER TABLE obras ADD COLUMN servicos TEXT NOT NULL DEFAULT 'planejamento,custos,qualidade'")
        # o cronograma de exemplo antigo tinha o nome de uma obra real; troca nas cópias já importadas
        con.execute(
            "UPDATE cronogramas SET xml = replace(replace(xml, 'Rubi – Obra teste', 'Residencial Exemplo – Torre A'), 'Rubi teste.xml', 'Obra de exemplo.xml') "
            "WHERE arquivo = 'cronograma_exemplo.xml' AND instr(xml, 'Rubi') > 0"
        )
    migrar_para_obras()


def _colunas_novas() -> None:
    """Bancos de antes das obras: acrescenta as colunas novas de usuarios."""
    with conectar() as con:
        if not con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='usuarios'").fetchone():
            return
        tem = {r[1] for r in con.execute("PRAGMA table_info(usuarios)")}
        for col, tipo in (("admin", "INTEGER NOT NULL DEFAULT 0"), ("obra_atual", "INTEGER"), ("ultimo_login", "TEXT"), ("whatsapp", "TEXT")):
            if col not in tem:
                con.execute(  # sql-seguro: coluna e tipo fixos no código
                    f"ALTER TABLE usuarios ADD COLUMN {col} {tipo}")


def migrar_para_obras() -> None:
    """Até out/2026 cada login tinha uma obra só, guardada com o id do usuário. Aqui cada usuário antigo
    vira dono de uma obra com o mesmo id (os dados ficam onde estão) e as tabelas de dados passam a
    apontar para obras. Antes de mexer, guarda uma cópia do banco na pasta de cópias de segurança."""
    with conectar() as con:
        if con.execute("PRAGMA user_version").fetchone()[0] >= 1:
            return
        antigas = [t for t in TABELAS_DA_OBRA if (r := con.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (t,)).fetchone()) and "REFERENCES usuarios(id)" in r[0]]
        usuarios_ = con.execute("SELECT id, email, nome, criado_em FROM usuarios").fetchall()
    if usuarios_:
        copia_de_seguranca("antes-das-obras")
    con = sqlite3.connect(CAMINHO, isolation_level=None)
    try:
        con.execute("PRAGMA foreign_keys = OFF")  # recriar as tabelas não pode apagar dados em cascata
        con.execute("BEGIN")
        for u in usuarios_:
            c = con.execute("SELECT arquivo FROM cronogramas WHERE usuario_id=?", (u[0],)).fetchone()
            if u[1].lower() == "demo@custoplano.com.br":
                nome = "Obra de demonstração"
            elif c:
                nome = os.path.splitext(c[0])[0] or "Obra"
            else:
                nome = u[2] or u[1].split("@")[0]
            con.execute("INSERT OR IGNORE INTO obras (id, nome, criado_em) VALUES (?, ?, ?)", (u[0], nome, u[3]))
            con.execute("INSERT OR IGNORE INTO acessos (usuario_id, obra_id, papel) VALUES (?, ?, 'editor')", (u[0], u[0]))
            con.execute("UPDATE usuarios SET obra_atual=? WHERE id=?", (u[0], u[0]))
        for t in antigas:
            sql = con.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (t,)).fetchone()[0]
            novo = re.sub(r'^CREATE TABLE\s+(IF NOT EXISTS\s+)?"?' + t + r'"?', f"CREATE TABLE {t}__novo", sql, count=1)
            con.execute(novo.replace("REFERENCES usuarios(id)", "REFERENCES obras(id)"))
            # sql-seguro abaixo: t vem da lista fixa TABELAS_DA_OBRA
            con.execute(  # sql-seguro
                f"INSERT INTO {t}__novo SELECT * FROM {t}")
            con.execute(  # sql-seguro
                f"DROP TABLE {t}")
            con.execute(  # sql-seguro
                f"ALTER TABLE {t}__novo RENAME TO {t}")
        if con.execute("PRAGMA foreign_key_check").fetchall():
            raise RuntimeError("dados sem obra depois da migração")
        con.execute("PRAGMA user_version = 1")
        con.execute("COMMIT")
        if usuarios_:
            print(f"Banco convertido para obras: {len(usuarios_)} obra(s), {len(antigas)} tabela(s) recriada(s).", flush=True)
    except Exception:
        con.execute("ROLLBACK")
        raise
    finally:
        con.close()
    with conectar() as c2:  # índices das tabelas recriadas
        c2.executescript(ESQUEMA)


# ------------------------------------------------------- cópias de segurança
def pasta_copias() -> str:
    return os.environ.get("CP_COPIAS", os.path.join(os.path.dirname(os.path.abspath(CAMINHO)), "copias"))


def copia_de_seguranca(rotulo: str = "") -> str:
    """Cópia consistente do banco inteiro (com as fotos), compactada, na pasta de cópias."""
    os.makedirs(pasta_copias(), exist_ok=True)
    nome = "custoplano-" + dt.datetime.now().strftime("%Y-%m-%d-%H%M") + (f"-{rotulo}" if rotulo else "") + ".db.gz"
    destino = os.path.join(pasta_copias(), nome)
    tmp = destino + ".tmp"
    origem = sqlite3.connect(CAMINHO)
    try:
        bruto = tmp + ".db"
        alvo = sqlite3.connect(bruto)
        with alvo:
            origem.backup(alvo)
        alvo.close()
    finally:
        origem.close()
    with open(bruto, "rb") as f, gzip.open(tmp, "wb", compresslevel=6) as g:
        shutil.copyfileobj(f, g)
    os.remove(bruto)
    os.replace(tmp, destino)
    return destino


def listar_copias() -> list[dict]:
    if not os.path.isdir(pasta_copias()):
        return []
    lista = []
    for n in sorted(os.listdir(pasta_copias()), reverse=True):
        if n.endswith(".db.gz"):
            lista.append({"nome": n, "bytes": os.path.getsize(os.path.join(pasta_copias(), n))})
    return lista


def limpar_copias(manter: int = 7) -> None:
    """Fica com as últimas cópias diárias, ocupando no máximo 40% do disco (sempre sobra ao menos a mais
    recente). As feitas antes de uma mudança grande ("-antes-") só saem quando passam de 3."""
    pasta = pasta_copias()
    copias = listar_copias()
    diarias = [c for c in copias if "-antes-" not in c["nome"]]
    antes = [c for c in copias if "-antes-" in c["nome"]]
    for c in diarias[manter:] + antes[3:]:
        os.remove(os.path.join(pasta, c["nome"]))
    diarias, antes = diarias[:manter], antes[:3]
    limite = shutil.disk_usage(pasta).total * 0.4
    while len(diarias) > 1 and sum(c["bytes"] for c in diarias + antes) > limite:
        os.remove(os.path.join(pasta, diarias.pop()["nome"]))


def agora() -> str:
    return dt.datetime.now().isoformat(timespec="seconds")


# ------------------------------------------------------------------ usuários
def criar_usuario(email: str, senha: str | None = None, nome: str = "", admin: bool = False) -> int:
    """Sem senha, a conta só entra depois de o usuário criar a senha pelo link de acesso."""
    with conectar() as con:
        cur = con.execute(
            "INSERT INTO usuarios (email, nome, senha_hash, criado_em, admin) VALUES (?, ?, ?, ?, ?)",
            (email.strip(), nome.strip(), generate_password_hash(senha or secrets.token_urlsafe(32)), agora(), 1 if admin else 0),
        )
        return cur.lastrowid


def tornar_admin(uid: int) -> None:
    with conectar() as con:
        con.execute("UPDATE usuarios SET admin = 1 WHERE id = ?", (uid,))


def trocar_senha(email: str, senha: str) -> bool:
    with conectar() as con:
        cur = con.execute("UPDATE usuarios SET senha_hash = ? WHERE email = ?", (generate_password_hash(senha), email.strip()))
        return cur.rowcount > 0


def autenticar(email: str, senha: str):
    with conectar() as con:
        u = con.execute("SELECT * FROM usuarios WHERE lower(email) = lower(?)", (email.strip(),)).fetchone()
    if u and check_password_hash(u["senha_hash"], senha):
        return u
    return None


def versao_senha(uid: int) -> str | None:
    """Pedaço do hash da senha guardado na sessão: muda quando a senha muda."""
    with conectar() as con:
        r = con.execute("SELECT senha_hash FROM usuarios WHERE id = ?", (uid,)).fetchone()
    return r["senha_hash"][-16:] if r else None


def usuario(uid: int):
    with conectar() as con:
        return con.execute("SELECT id, email, nome, admin, obra_atual FROM usuarios WHERE id = ?", (uid,)).fetchone()


def usuario_por_email(email: str):
    with conectar() as con:
        return con.execute("SELECT id, email, nome, admin FROM usuarios WHERE lower(email) = lower(?)", (email.strip(),)).fetchone()


def definir_whatsapp(uid: int, numero: str | None) -> None:
    with conectar() as con:
        con.execute("UPDATE usuarios SET whatsapp = ? WHERE id = ?", (numero or None, uid))


def usuario_por_whatsapp(chave_numero, chave):
    """Usuário dono do número (chave() iguala o celular com e sem o 9 da frente)."""
    with conectar() as con:
        for r in con.execute("SELECT id, email, nome, admin, whatsapp FROM usuarios WHERE whatsapp IS NOT NULL AND whatsapp <> ''"):
            if chave(r["whatsapp"]) == chave_numero:
                return r
    return None


def remover_usuario(uid: int) -> None:
    with conectar() as con:
        con.execute("DELETE FROM usuarios WHERE id = ?", (uid,))


def registrar_entrada(uid: int, obra_id: int | None) -> None:
    with conectar() as con:
        con.execute("UPDATE usuarios SET ultimo_login = ?, obra_atual = COALESCE(?, obra_atual) WHERE id = ?", (agora(), obra_id, uid))


# ---------------------------------------------------------------------- obras
def obras_do_usuario(uid: int) -> list[dict]:
    """Obras que o usuário pode abrir, com o papel dele em cada uma. A equipe Custo Plano vê todas."""
    with conectar() as con:
        u = con.execute("SELECT admin FROM usuarios WHERE id = ?", (uid,)).fetchone()
        if u and u["admin"]:
            rs = con.execute("SELECT id, nome, empresa, servicos, 'editor' AS papel FROM obras ORDER BY empresa, nome").fetchall()
        else:
            rs = con.execute("SELECT o.id, o.nome, o.empresa, o.servicos, a.papel FROM acessos a JOIN obras o ON o.id = a.obra_id "
                             "WHERE a.usuario_id = ? ORDER BY o.empresa, o.nome", (uid,)).fetchall()
    return [dict(r, servicos=_servicos(r["servicos"])) for r in rs]


# Serviços que a construtora pode contratar; cada obra guarda os seus e o sistema mostra só esses
SERVICOS = ("planejamento", "custos", "qualidade")


def _servicos(texto: str | None) -> list[str]:
    tem = set((texto or "").split(","))
    return [s for s in SERVICOS if s in tem]


def servicos_da_obra(obra_id: int) -> list[str]:
    with conectar() as con:
        r = con.execute("SELECT servicos FROM obras WHERE id = ?", (obra_id,)).fetchone()
    return _servicos(r["servicos"]) if r else []


def definir_servicos(obra_id: int, servicos: list[str]) -> list[str]:
    lista = [s for s in SERVICOS if s in set(servicos)]
    if not lista:
        raise ValueError("A obra precisa de pelo menos um serviço.")
    with conectar() as con:
        con.execute("UPDATE obras SET servicos = ? WHERE id = ?", (",".join(lista), obra_id))
    return lista


def papel_na_obra(uid: int, obra_id: int) -> str | None:
    """editor, leitor ou None (sem acesso)."""
    with conectar() as con:
        u = con.execute("SELECT admin FROM usuarios WHERE id = ?", (uid,)).fetchone()
        if not u:
            return None
        if u["admin"]:
            return "editor" if con.execute("SELECT 1 FROM obras WHERE id = ?", (obra_id,)).fetchone() else None
        r = con.execute("SELECT papel FROM acessos WHERE usuario_id = ? AND obra_id = ?", (uid, obra_id)).fetchone()
    return r["papel"] if r else None


def obra(obra_id: int):
    with conectar() as con:
        return con.execute("SELECT * FROM obras WHERE id = ?", (obra_id,)).fetchone()


def criar_obra(nome: str, empresa: str = "", servicos: list[str] | None = None) -> int:
    lista = [s for s in SERVICOS if s in set(servicos)] if servicos is not None else list(SERVICOS)
    if not lista:
        raise ValueError("A obra precisa de pelo menos um serviço.")
    with conectar() as con:
        return con.execute("INSERT INTO obras (nome, empresa, criado_em, servicos) VALUES (?, ?, ?, ?)",
                           (nome.strip(), empresa.strip(), agora(), ",".join(lista))).lastrowid


def editar_obra(obra_id: int, nome: str, empresa: str) -> None:
    with conectar() as con:
        con.execute("UPDATE obras SET nome = ?, empresa = ? WHERE id = ?", (nome.strip(), empresa.strip(), obra_id))


def remover_obra(obra_id: int) -> None:
    """Apaga a obra e todos os dados dela (cronograma, medições, custos, qualidade, fotos)."""
    with conectar() as con:
        con.execute("DELETE FROM obras WHERE id = ?", (obra_id,))


def apagar_de_vez(obra_id: int) -> None:
    """Para dados que não podem ficar guardados: apaga a obra, reescreve o arquivo do banco (VACUUM) para não
    sobrar nada dela no disco, tira todas as cópias de segurança antigas (que ainda têm a obra) e faz uma
    cópia nova, já sem ela."""
    remover_obra(obra_id)
    con = sqlite3.connect(CAMINHO)
    try:
        con.execute("VACUUM")
        con.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    finally:
        con.close()
    for c in listar_copias():
        os.remove(os.path.join(pasta_copias(), c["nome"]))
    copia_de_seguranca()


def definir_acesso(uid: int, obra_id: int, papel: str | None) -> None:
    with conectar() as con:
        if papel is None:
            con.execute("DELETE FROM acessos WHERE usuario_id = ? AND obra_id = ?", (uid, obra_id))
        else:
            con.execute("INSERT INTO acessos (usuario_id, obra_id, papel) VALUES (?, ?, ?) "
                        "ON CONFLICT(usuario_id, obra_id) DO UPDATE SET papel = excluded.papel", (uid, obra_id, papel))


def painel_admin() -> dict:
    """Obras e usuários para a tela de administração."""
    with conectar() as con:
        obras_ = [dict(r) for r in con.execute(
            "SELECT o.id, o.nome, o.empresa, o.criado_em, o.servicos, c.arquivo AS cronograma, c.atualizado_em AS atualizado_em, "
            "(SELECT COUNT(*) FROM acessos a WHERE a.obra_id = o.id) AS usuarios, "
            "(SELECT COALESCE(SUM(LENGTH(f.dados)), 0) FROM fotos f WHERE f.usuario_id = o.id) AS bytes_fotos "
            "FROM obras o LEFT JOIN cronogramas c ON c.usuario_id = o.id ORDER BY o.empresa, o.nome")]
        usuarios_ = [dict(r) for r in con.execute(
            "SELECT id, email, nome, admin, criado_em, ultimo_login, whatsapp FROM usuarios ORDER BY admin DESC, email")]
        acessos_ = con.execute("SELECT usuario_id, obra_id, papel FROM acessos").fetchall()
    por_u: dict = {}
    for a in acessos_:
        por_u.setdefault(a["usuario_id"], []).append({"obra_id": a["obra_id"], "papel": a["papel"]})
    for u in usuarios_:
        u["acessos"] = por_u.get(u["id"], [])
    for o in obras_:
        o["servicos"] = _servicos(o["servicos"])
    return {"obras": obras_, "usuarios": usuarios_}


# --------------------------------------------------------- links de senha
def _hash_codigo(codigo: str) -> str:
    return hashlib.sha256(codigo.encode("utf-8")).hexdigest()


def criar_link_senha(uid: int, horas: int) -> str:
    """Código de uso único para criar ou trocar a senha. Um link novo invalida os anteriores."""
    codigo = secrets.token_urlsafe(32)
    expira = (dt.datetime.now() + dt.timedelta(hours=horas)).isoformat(timespec="seconds")
    with conectar() as con:
        con.execute("DELETE FROM links_senha WHERE usuario_id = ?", (uid,))
        con.execute("INSERT INTO links_senha (hash, usuario_id, expira_em, criado_em) VALUES (?, ?, ?, ?)",
                    (_hash_codigo(codigo), uid, expira, agora()))
    return codigo


def link_senha(codigo: str):
    """Usuário dono de um link ainda válido, ou None."""
    with conectar() as con:
        r = con.execute("SELECT l.usuario_id, l.expira_em, l.usado_em, u.email FROM links_senha l "
                        "JOIN usuarios u ON u.id = l.usuario_id WHERE l.hash = ?", (_hash_codigo(codigo or ""),)).fetchone()
    if not r or r["usado_em"] or r["expira_em"] < agora():
        return None
    return r


def usar_link_senha(codigo: str, senha: str) -> int | None:
    r = link_senha(codigo)
    if not r:
        return None
    with conectar() as con:
        cur = con.execute("UPDATE links_senha SET usado_em = ? WHERE hash = ? AND usado_em IS NULL", (agora(), _hash_codigo(codigo)))
        if cur.rowcount != 1:  # dois envios ao mesmo tempo: só o primeiro vale
            return None
        con.execute("UPDATE usuarios SET senha_hash = ? WHERE id = ?", (generate_password_hash(senha), r["usuario_id"]))
    return r["usuario_id"]


def trocar_senha_id(uid: int, senha: str) -> None:
    with conectar() as con:
        con.execute("UPDATE usuarios SET senha_hash = ? WHERE id = ?", (generate_password_hash(senha), uid))


def listar_usuarios():
    with conectar() as con:
        return con.execute("SELECT id, email, nome, criado_em FROM usuarios ORDER BY email").fetchall()


# ---------------------------------------------------------------- cronograma
def salvar_cronograma(usuario_id: int, arquivo: str, xml: str, peso: str, data_status: str, atual: dict) -> None:
    js = json.dumps(atual)
    with conectar() as con:
        con.execute(
            """INSERT INTO cronogramas (usuario_id, arquivo, xml, peso, data_status, atual, anterior, atualizado_em)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(usuario_id) DO UPDATE SET arquivo=excluded.arquivo, xml=excluded.xml, peso=excluded.peso,
                 data_status=excluded.data_status, atual=excluded.atual, anterior=excluded.anterior,
                 atualizado_em=excluded.atualizado_em""",
            (usuario_id, arquivo, xml, peso, data_status, js, js, agora()),
        )


def cronograma(usuario_id: int):
    with conectar() as con:
        return con.execute("SELECT * FROM cronogramas WHERE usuario_id = ?", (usuario_id,)).fetchone()


def salvar_medicao(usuario_id: int, atual: dict, peso: str, data_status: str, anterior: dict | None = None) -> None:
    with conectar() as con:
        if anterior is None:
            con.execute("UPDATE cronogramas SET atual=?, peso=?, data_status=?, atualizado_em=? WHERE usuario_id=?",
                        (json.dumps(atual), peso, data_status, agora(), usuario_id))
        else:
            con.execute("UPDATE cronogramas SET atual=?, anterior=?, peso=?, data_status=?, atualizado_em=? WHERE usuario_id=?",
                        (json.dumps(atual), json.dumps(anterior), peso, data_status, agora(), usuario_id))


def remover_cronograma(usuario_id: int) -> None:
    with conectar() as con:
        con.execute("DELETE FROM cronogramas WHERE usuario_id = ?", (usuario_id,))


# ------------------------------------------------------------------ histórico
def fechar_medicao(usuario_id: int, real: float, prev: float) -> None:
    """Guarda a medição atual no histórico e faz dela a 'medição anterior'."""
    c = cronograma(usuario_id)
    if not c:
        return
    with conectar() as con:
        con.execute(
            """INSERT INTO medicoes_fechadas (usuario_id, arquivo, data_status, fechado_em, avanco_real, avanco_prev, peso, xml, atual, anterior)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (usuario_id, c["arquivo"], c["data_status"], agora(), real, prev, c["peso"], c["xml"], c["atual"], c["anterior"]),
        )
        con.execute("UPDATE cronogramas SET anterior = atual, atualizado_em = ? WHERE usuario_id = ?", (agora(), usuario_id))


def historico(usuario_id: int):
    with conectar() as con:
        return con.execute(
            "SELECT id, arquivo, data_status, fechado_em, avanco_real, avanco_prev FROM medicoes_fechadas WHERE usuario_id=? ORDER BY id DESC",
            (usuario_id,),
        ).fetchall()


# ---------------------------------------------------- plano de ataque (escadinha)
def salvar_plano(usuario_id: int, arquivo: str, dados: dict) -> None:
    with conectar() as con:
        con.execute(
            """INSERT INTO planos_ataque (usuario_id, arquivo, dados, atualizado_em) VALUES (?, ?, ?, ?)
               ON CONFLICT(usuario_id) DO UPDATE SET arquivo=excluded.arquivo, dados=excluded.dados,
                 atualizado_em=excluded.atualizado_em""",
            (usuario_id, arquivo, json.dumps(dados, ensure_ascii=False), agora()),
        )


def plano(usuario_id: int):
    with conectar() as con:
        return con.execute("SELECT * FROM planos_ataque WHERE usuario_id = ?", (usuario_id,)).fetchone()


def remover_plano(usuario_id: int) -> None:
    with conectar() as con:
        con.execute("DELETE FROM planos_ataque WHERE usuario_id = ?", (usuario_id,))


# --------------------------------------------------------------- suprimentos
def suprimentos(usuario_id: int) -> dict:
    with conectar() as con:
        r = con.execute("SELECT dados FROM suprimentos WHERE usuario_id = ?", (usuario_id,)).fetchone()
    return json.loads(r["dados"]) if r else {}


def salvar_suprimentos(usuario_id: int, dados: dict) -> None:
    with conectar() as con:
        con.execute(
            """INSERT INTO suprimentos (usuario_id, dados, atualizado_em) VALUES (?, ?, ?)
               ON CONFLICT(usuario_id) DO UPDATE SET dados=excluded.dados, atualizado_em=excluded.atualizado_em""",
            (usuario_id, json.dumps(dados, ensure_ascii=False), agora()),
        )


# --------------------------------------------------------------- curto prazo
def curto_prazo(usuario_id: int) -> dict:
    with conectar() as con:
        r = con.execute("SELECT dados FROM curto_prazo WHERE usuario_id = ?", (usuario_id,)).fetchone()
    return json.loads(r["dados"]) if r else {}


def salvar_curto_prazo(usuario_id: int, dados: dict) -> None:
    with conectar() as con:
        con.execute(
            """INSERT INTO curto_prazo (usuario_id, dados, atualizado_em) VALUES (?, ?, ?)
               ON CONFLICT(usuario_id) DO UPDATE SET dados=excluded.dados, atualizado_em=excluded.atualizado_em""",
            (usuario_id, json.dumps(dados, ensure_ascii=False), agora()),
        )


# --------------------------------------------------------- orçado x realizado
def controle_custos(usuario_id: int) -> dict | None:
    with conectar() as con:
        r = con.execute("SELECT arquivo, dados, atualizado_em FROM controle_custos WHERE usuario_id = ?", (usuario_id,)).fetchone()
    return dict(json.loads(r["dados"]), arquivo=r["arquivo"], atualizado_em=r["atualizado_em"]) if r else None


def salvar_controle_custos(usuario_id: int, arquivo: str, dados: dict) -> None:
    with conectar() as con:
        con.execute(
            """INSERT INTO controle_custos (usuario_id, arquivo, dados, atualizado_em) VALUES (?, ?, ?, ?)
               ON CONFLICT(usuario_id) DO UPDATE SET arquivo=excluded.arquivo, dados=excluded.dados,
                 atualizado_em=excluded.atualizado_em""",
            (usuario_id, arquivo, json.dumps(dados, ensure_ascii=False), agora()),
        )


def remover_controle_custos(usuario_id: int) -> None:
    with conectar() as con:
        con.execute("DELETE FROM controle_custos WHERE usuario_id = ?", (usuario_id,))


# -------------------------------------------------------------------- custos
def salvar_orcamento(usuario_id: int, arquivo: str, dados: dict) -> None:
    """Grava o orçamento; os lançamentos feitos pelo sistema são mantidos."""
    with conectar() as con:
        con.execute(
            """INSERT INTO custos (usuario_id, arquivo, dados, atualizado_em) VALUES (?, ?, ?, ?)
               ON CONFLICT(usuario_id) DO UPDATE SET arquivo=excluded.arquivo, dados=excluded.dados,
                 atualizado_em=excluded.atualizado_em""",
            (usuario_id, arquivo, json.dumps(dados, ensure_ascii=False), agora()),
        )


def custos(usuario_id: int):
    with conectar() as con:
        return con.execute("SELECT * FROM custos WHERE usuario_id = ?", (usuario_id,)).fetchone()


def salvar_lancamentos(usuario_id: int, lancamentos: list) -> None:
    with conectar() as con:
        con.execute("UPDATE custos SET lancamentos = ?, atualizado_em = ? WHERE usuario_id = ?",
                    (json.dumps(lancamentos, ensure_ascii=False), agora(), usuario_id))


def remover_custos(usuario_id: int) -> None:
    with conectar() as con:
        con.execute("DELETE FROM custos WHERE usuario_id = ?", (usuario_id,))


def medicao_fechada(usuario_id: int, mid: int):
    with conectar() as con:
        return con.execute("SELECT * FROM medicoes_fechadas WHERE usuario_id=? AND id=?", (usuario_id, mid)).fetchone()


def cronogramas_grandes():
    """(usuario_id) dos cronogramas gravados com recursos/atribuições/calendários (antes do XML enxuto)."""
    with conectar() as con:
        return [r[0] for r in con.execute(
            "SELECT usuario_id FROM cronogramas WHERE instr(xml, '<Assignments') > 0 OR instr(xml, '<Resources') > 0 "
            "OR instr(xml, '<Calendars') > 0").fetchall()]


def trocar_xml(usuario_id: int, xml: str) -> None:
    with conectar() as con:
        con.execute("UPDATE cronogramas SET xml = ? WHERE usuario_id = ?", (xml, usuario_id))
