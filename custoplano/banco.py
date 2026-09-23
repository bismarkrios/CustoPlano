"""Banco de dados SQLite do Custo Plano: usuários, cronogramas, medições e histórico."""
from __future__ import annotations

import datetime as dt
import json
import os
import sqlite3

from werkzeug.security import check_password_hash, generate_password_hash

CAMINHO = os.environ.get("CP_BANCO", os.path.join(os.path.dirname(os.path.abspath(__file__)), "custoplano.db"))

ESQUEMA = """
CREATE TABLE IF NOT EXISTS usuarios (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    email       TEXT NOT NULL UNIQUE COLLATE NOCASE,
    nome        TEXT NOT NULL DEFAULT '',
    senha_hash  TEXT NOT NULL,
    criado_em   TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS cronogramas (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    usuario_id   INTEGER NOT NULL UNIQUE REFERENCES usuarios(id) ON DELETE CASCADE,
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
    usuario_id    INTEGER NOT NULL REFERENCES usuarios(id) ON DELETE CASCADE,
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
"""


def conectar() -> sqlite3.Connection:
    con = sqlite3.connect(CAMINHO)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    return con


def iniciar() -> None:
    with conectar() as con:
        con.executescript(ESQUEMA)


def agora() -> str:
    return dt.datetime.now().isoformat(timespec="seconds")


# ------------------------------------------------------------------ usuários
def criar_usuario(email: str, senha: str, nome: str = "") -> int:
    with conectar() as con:
        cur = con.execute(
            "INSERT INTO usuarios (email, nome, senha_hash, criado_em) VALUES (?, ?, ?, ?)",
            (email.strip(), nome.strip(), generate_password_hash(senha), agora()),
        )
        return cur.lastrowid


def trocar_senha(email: str, senha: str) -> bool:
    with conectar() as con:
        cur = con.execute("UPDATE usuarios SET senha_hash = ? WHERE email = ?", (generate_password_hash(senha), email.strip()))
        return cur.rowcount > 0


def autenticar(email: str, senha: str):
    with conectar() as con:
        u = con.execute("SELECT * FROM usuarios WHERE email = ?", (email.strip(),)).fetchone()
    if u and check_password_hash(u["senha_hash"], senha):
        return u
    return None


def usuario(uid: int):
    with conectar() as con:
        return con.execute("SELECT id, email, nome FROM usuarios WHERE id = ?", (uid,)).fetchone()


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


def medicao_fechada(usuario_id: int, mid: int):
    with conectar() as con:
        return con.execute("SELECT * FROM medicoes_fechadas WHERE usuario_id=? AND id=?", (usuario_id, mid)).fetchone()
