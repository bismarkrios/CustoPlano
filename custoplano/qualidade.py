"""
Qualidade da obra (PBQP-H / ISO 9001): modelos de FVS, inspeções, não conformidades e fotos.

FVS = Ficha de Verificação de Serviço. Cada inspeção aplica um modelo a um local (pavimento,
unidade, área) e marca cada item como conforme, não conforme ou não se aplica. Item não
conforme abre uma não conformidade (NC) automaticamente; a reinspeção aprovada fecha as NCs
da inspeção original.
"""
from __future__ import annotations

import base64
import datetime as dt
import json
import math
import re
import secrets

import banco

ESQUEMA = """
CREATE TABLE IF NOT EXISTS fvs_modelos (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    usuario_id  INTEGER NOT NULL REFERENCES obras(id) ON DELETE CASCADE,
    codigo      TEXT NOT NULL,
    servico     TEXT NOT NULL,
    itens       TEXT NOT NULL,              -- JSON [{id, item, criterio}]
    criado_em   TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS inspecoes (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    usuario_id    INTEGER NOT NULL REFERENCES obras(id) ON DELETE CASCADE,
    cid           TEXT,                     -- id gerado no aparelho (evita duplicar envios offline)
    modelo_id     INTEGER,
    codigo        TEXT NOT NULL,
    servico       TEXT NOT NULL,
    local         TEXT NOT NULL,
    data          TEXT NOT NULL,
    inspetor      TEXT NOT NULL DEFAULT '',
    resultado     TEXT NOT NULL,            -- aprovada | reprovada
    itens         TEXT NOT NULL,            -- JSON [{id, item, criterio, status, obs, fotos}]
    obs           TEXT NOT NULL DEFAULT '',
    reinspecao_de INTEGER,
    assinatura    TEXT,                     -- id da foto com a assinatura
    criado_em     TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS ncs (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    usuario_id   INTEGER NOT NULL REFERENCES obras(id) ON DELETE CASCADE,
    cid          TEXT,
    numero       INTEGER NOT NULL,
    origem       TEXT NOT NULL,             -- FVS | Auditoria | Cliente | Obra
    inspecao_id  INTEGER,
    servico      TEXT NOT NULL DEFAULT '',
    local        TEXT NOT NULL DEFAULT '',
    descricao    TEXT NOT NULL,
    gravidade    TEXT NOT NULL DEFAULT 'media',
    responsavel  TEXT NOT NULL DEFAULT '',
    prazo        TEXT,
    causa        TEXT NOT NULL DEFAULT '',
    acao         TEXT NOT NULL DEFAULT '',
    status       TEXT NOT NULL DEFAULT 'aberta',   -- aberta | tratamento | fechada
    eficacia     TEXT NOT NULL DEFAULT '',
    fotos        TEXT NOT NULL DEFAULT '[]',
    fechada_em   TEXT,
    criado_em    TEXT NOT NULL,
    atualizado_em TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS fotos (
    id          TEXT PRIMARY KEY,
    usuario_id  INTEGER NOT NULL REFERENCES obras(id) ON DELETE CASCADE,
    mime        TEXT NOT NULL,
    dados       BLOB NOT NULL,
    criado_em   TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS fvs_tarefas (
    usuario_id    INTEGER PRIMARY KEY REFERENCES obras(id) ON DELETE CASCADE,
    dados         TEXT NOT NULL,            -- JSON {uid da tarefa do cronograma: {modelo_id}}; 0 = sem FVS
    atualizado_em TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_insp_usuario ON inspecoes(usuario_id);
CREATE INDEX IF NOT EXISTS ix_nc_usuario ON ncs(usuario_id);
"""


class ErroQualidade(Exception):
    pass


def iniciar() -> None:
    with banco.conectar() as con:
        con.executescript(ESQUEMA)


# ------------------------------------------------------ biblioteca de FVS
# Critérios de referência, para a equipe de qualidade ajustar aos procedimentos da empresa.
BIBLIOTECA = [
    ("FVS-01", "Alvenaria de vedação", [
        ("Marcação e locação", "Paredes e vãos conforme projeto; desvio máximo de 5 mm"),
        ("Prumo", "Desvio máximo de 3 mm por metro (prumo ou nível a laser)"),
        ("Nível e alinhamento das fiadas", "Fiadas niveladas e alinhadas; sem desvio visível"),
        ("Juntas de assentamento", "Espessura de 10 mm ± 3 mm, preenchidas"),
        ("Amarração com a estrutura", "Tela ou ferro-cabelo conforme projeto, a cada 2 fiadas"),
        ("Vergas e contravergas", "Conforme projeto, com transpasse mínimo de 20 cm de cada lado"),
        ("Fixação superior (encunhamento)", "Executada após o prazo definido, sem vazios"),
        ("Limpeza", "Sem rebarbas de argamassa nas faces e no piso"),
    ]),
    ("FVS-02", "Fôrmas e armação", [
        ("Dimensões e locação das fôrmas", "Conforme projeto; desvio máximo de 5 mm"),
        ("Prumo e nível das fôrmas", "Pilares aprumados e fundos de viga/laje nivelados"),
        ("Escoramento e travamento", "Conforme projeto de escoramento, sem folgas"),
        ("Armadura", "Bitolas, quantidades, espaçamentos e ancoragens conforme projeto"),
        ("Cobrimento", "Espaçadores posicionados garantindo o cobrimento de projeto"),
        ("Limpeza e desmoldante", "Fôrmas limpas, estanques e com desmoldante antes da concretagem"),
    ]),
    ("FVS-03", "Concretagem", [
        ("Recebimento do concreto", "Nota fiscal com fck, slump e volume conforme pedido; slump conferido"),
        ("Corpos de prova", "Moldados e identificados por caminhão, conforme plano de controle"),
        ("Lançamento e adensamento", "Sem segregação, ninhos ou juntas frias"),
        ("Nível e acabamento da laje", "Nível conforme projeto; superfície desempenada"),
        ("Cura", "Iniciada logo após o acabamento e mantida pelo prazo definido"),
    ]),
    ("FVS-04", "Contrapiso", [
        ("Base preparada", "Laje limpa, sem restos de argamassa, com ponte de aderência"),
        ("Nível e caimento", "Conforme projeto; caimento para ralos nas áreas molhadas"),
        ("Espessura", "Conforme projeto, conferida nas taliscas"),
        ("Planeza", "Desvio máximo de 3 mm sob régua de 2 m"),
        ("Aderência", "Sem som cavo no teste de percussão"),
        ("Acabamento", "Superfície desempenada, sem fissuras"),
    ]),
    ("FVS-05", "Revestimento argamassado (emboço/reboco)", [
        ("Base preparada", "Chapisco executado e curado; base limpa"),
        ("Taliscas e mestras", "Posicionadas conforme espessura prevista"),
        ("Prumo", "Desvio máximo de 3 mm por metro"),
        ("Planeza", "Desvio máximo de 3 mm sob régua de 2 m"),
        ("Aderência", "Sem som cavo no teste de percussão"),
        ("Esquadro e arestas", "Cantos em esquadro e arestas retilíneas"),
        ("Acabamento", "Sem fissuras, bolhas ou ondulações"),
    ]),
    ("FVS-06", "Revestimento cerâmico (piso e parede)", [
        ("Material", "Peças conforme especificação, mesmo lote e tonalidade"),
        ("Argamassa colante", "Tipo especificado (AC-I, AC-II ou AC-III), dupla colagem quando exigido"),
        ("Paginação e juntas", "Juntas alinhadas, com espessura conforme projeto"),
        ("Planeza e nível", "Sem dentes entre peças; caimento para ralos nas áreas molhadas"),
        ("Aderência", "Sem peças ocas no teste de percussão"),
        ("Rejuntamento", "Rejunte uniforme, sem falhas; peças limpas"),
        ("Recortes e arremates", "Recortes sem lascas; arremates em ralos, soleiras e rodapés"),
    ]),
    ("FVS-07", "Gesso liso", [
        ("Base", "Superfície limpa e sem partes soltas"),
        ("Planeza", "Desvio máximo de 3 mm sob régua de 2 m"),
        ("Esquadro e arestas", "Cantos em esquadro, arestas vivas"),
        ("Acabamento", "Sem fissuras, bolhas ou marcas de desempenadeira"),
        ("Limpeza", "Piso e esquadrias limpos de respingos"),
    ]),
    ("FVS-08", "Pintura", [
        ("Preparação da superfície", "Lixada, limpa e com massa onde especificado"),
        ("Produto e cor", "Conforme especificação do projeto"),
        ("Número de demãos", "Conforme especificação, respeitando o intervalo entre demãos"),
        ("Uniformidade", "Sem manchas, escorrimentos ou marcas de rolo"),
        ("Recortes", "Recortes limpos em tetos, rodapés e esquadrias"),
        ("Proteção", "Pisos, esquadrias e louças protegidos"),
    ]),
    ("FVS-09", "Impermeabilização", [
        ("Base", "Regularizada, limpa, seca e com cantos arredondados"),
        ("Produto e camadas", "Sistema, consumo e número de camadas conforme projeto"),
        ("Rodapé e detalhes", "Subida nas paredes, ralos e passagens conforme projeto"),
        ("Teste de estanqueidade", "Lâmina d'água por 72 h, sem vazamentos"),
        ("Proteção mecânica", "Executada logo após o teste"),
    ]),
    ("FVS-10", "Instalações hidrossanitárias", [
        ("Traçado e pontos", "Posição e altura dos pontos conforme projeto"),
        ("Fixação", "Tubulações fixadas com suportes adequados"),
        ("Declividade do esgoto", "Conforme projeto (mínimo de 1% a 2%, conforme o diâmetro)"),
        ("Teste de pressão (água)", "Sem vazamentos durante o teste"),
        ("Teste de estanqueidade (esgoto)", "Sem vazamentos durante o teste"),
        ("Proteção das pontas", "Pontas tamponadas para evitar entupimentos"),
    ]),
    ("FVS-11", "Instalações elétricas", [
        ("Eletrodutos e caixas", "Posição, altura e quantidade conforme projeto"),
        ("Fixação das caixas", "Caixas niveladas e faceando o revestimento"),
        ("Enfiação", "Bitolas e cores dos condutores conforme projeto"),
        ("Quadro de distribuição", "Circuitos identificados, disjuntores conforme projeto"),
        ("Testes", "Continuidade, isolamento e funcionamento de pontos"),
        ("Aterramento", "Condutor de proteção em todas as tomadas"),
    ]),
    ("FVS-12", "Esquadrias", [
        ("Contramarco", "Nivelado, aprumado e em esquadro"),
        ("Vedação", "Selante contínuo na interface com a alvenaria"),
        ("Funcionamento", "Abertura e fechamento sem esforço; fechos funcionando"),
        ("Drenos", "Livres e na posição correta"),
        ("Integridade", "Vidros e perfis sem riscos ou danos"),
        ("Proteção e limpeza", "Protegidas até a entrega"),
    ]),
]


def _agora() -> str:
    return dt.datetime.now().isoformat(timespec="seconds")


def _itens_json(itens) -> str:
    out = []
    for i, it in enumerate(itens):
        if isinstance(it, (list, tuple)):
            item, crit = it
            iid = f"i{i + 1}"
        else:
            item, crit = str(it.get("item") or "").strip(), str(it.get("criterio") or "").strip()
            iid = str(it.get("id") or f"i{i + 1}")
        if item:
            out.append({"id": iid, "item": item[:160], "criterio": crit[:300]})
    return json.dumps(out, ensure_ascii=False)


def garantir_biblioteca(uid: int) -> None:
    with banco.conectar() as con:
        if con.execute("SELECT 1 FROM fvs_modelos WHERE usuario_id=? LIMIT 1", (uid,)).fetchone():
            return
        for cod, serv, itens in BIBLIOTECA:
            con.execute("INSERT INTO fvs_modelos (usuario_id, codigo, servico, itens, criado_em) VALUES (?,?,?,?,?)",
                        (uid, cod, serv, _itens_json(itens), _agora()))


# ------------------------------------------------------------------ fotos
RE_DATAURL = re.compile(r"^data:(image/(?:jpeg|png|webp));base64,(.+)$", re.S)
MAX_FOTO = 2_500_000
MAX_FOTOS_CONTA = 300_000_000  # o disco do servidor é dividido entre todas as contas


def guardar_foto(con, uid: int, valor: str) -> str | None:
    """Recebe um data URL (ou um id já existente) e devolve o id da foto."""
    if not isinstance(valor, str):
        return None
    if re.fullmatch(r"[0-9a-f]{16}", valor):  # foto já enviada: só vale se for do mesmo usuário
        dono = con.execute("SELECT 1 FROM fotos WHERE id=? AND usuario_id=?", (valor, uid)).fetchone()
        return valor if dono else None
    m = RE_DATAURL.match(valor)
    if not m:
        return None
    dados = base64.b64decode(m.group(2))
    if len(dados) > MAX_FOTO:
        raise ErroQualidade("Foto grande demais (máximo de 2,5 MB).")
    usado = con.execute("SELECT COALESCE(SUM(LENGTH(dados)), 0) FROM fotos WHERE usuario_id=?", (uid,)).fetchone()[0]
    if usado + len(dados) > MAX_FOTOS_CONTA:
        raise ErroQualidade("Limite de fotos da conta atingido (300 MB). Fale com a Custo Plano para ampliar.")
    fid = secrets.token_hex(8)
    con.execute("INSERT INTO fotos (id, usuario_id, mime, dados, criado_em) VALUES (?,?,?,?,?)",
                (fid, uid, m.group(1), dados, _agora()))
    return fid


def foto(uid: int, fid: str):
    with banco.conectar() as con:
        return con.execute("SELECT mime, dados FROM fotos WHERE id=? AND usuario_id=?", (fid, uid)).fetchone()


# ---------------------------------------------------------------- estado
def estado(uid: int) -> dict:
    garantir_biblioteca(uid)
    with banco.conectar() as con:
        modelos = [dict(r, itens=json.loads(r["itens"])) for r in
                   con.execute("SELECT id, codigo, servico, itens FROM fvs_modelos WHERE usuario_id=? ORDER BY codigo, id", (uid,))]
        insp = []
        for r in con.execute("SELECT * FROM inspecoes WHERE usuario_id=? ORDER BY data DESC, id DESC", (uid,)):
            d = dict(r)
            d["itens"] = json.loads(d["itens"])
            insp.append(d)
        ncs = []
        for r in con.execute("SELECT * FROM ncs WHERE usuario_id=? ORDER BY numero DESC", (uid,)):
            d = dict(r)
            d["fotos"] = json.loads(d["fotos"])
            ncs.append(d)
    # locais sugeridos: pavimentos do plano de ataque + locais já usados
    locais = []
    p = banco.plano(uid)
    if p:
        try:
            locais = [x["rotulo"] for x in json.loads(p["dados"])["atual"]["pavimentos"]]
        except (KeyError, ValueError):
            locais = []
    for i in insp:
        if i["local"] not in locais:
            locais.append(i["local"])
    return {"modelos": modelos, "inspecoes": insp, "ncs": ncs, "locais": locais, "tarefas": tarefas(uid),
            "hoje": dt.date.today().isoformat()}


# ------------------------------------------- FVS ligadas às tarefas do cronograma
def tarefas(uid: int) -> dict:
    with banco.conectar() as con:
        r = con.execute("SELECT dados FROM fvs_tarefas WHERE usuario_id=?", (uid,)).fetchone()
    return json.loads(r["dados"]) if r else {}


def ligar_tarefa(uid: int, d: dict) -> None:
    """Escolhe a FVS de uma tarefa do cronograma. modelo_id vazio volta para a escolha automática
    (pelo nome do serviço); 0 marca a tarefa como sem FVS."""
    tid = str(d.get("uid") or "")
    if not re.fullmatch(r"\d{1,9}", tid):
        raise ErroQualidade("Tarefa do cronograma inválida.")
    mid = d.get("modelo_id")
    dados = tarefas(uid)
    if mid is None or mid == "":
        dados.pop(tid, None)
    else:
        try:
            mid = int(mid)
        except (TypeError, ValueError):
            raise ErroQualidade("FVS inválida.") from None
        if mid:
            with banco.conectar() as con:
                if not con.execute("SELECT 1 FROM fvs_modelos WHERE id=? AND usuario_id=?", (mid, uid)).fetchone():
                    raise ErroQualidade("FVS não encontrada.")
        dados[tid] = {"modelo_id": mid}
    if len(dados) > 20000:
        raise ErroQualidade("Limite de tarefas atingido.")
    with banco.conectar() as con:
        con.execute("""INSERT INTO fvs_tarefas (usuario_id, dados, atualizado_em) VALUES (?, ?, ?)
                       ON CONFLICT(usuario_id) DO UPDATE SET dados=excluded.dados, atualizado_em=excluded.atualizado_em""",
                    (uid, json.dumps(dados), _agora()))


# --------------------------------------------------------------- modelos
def salvar_modelo(uid: int, d: dict) -> None:
    serv = str(d.get("servico") or "").strip()
    if not serv:
        raise ErroQualidade("Informe o serviço da FVS.")
    itens = _itens_json(d.get("itens") or [])
    if itens == "[]":
        raise ErroQualidade("A FVS precisa de pelo menos um item de verificação.")
    cod = str(d.get("codigo") or "").strip()[:20]
    with banco.conectar() as con:
        if d.get("id"):
            con.execute("UPDATE fvs_modelos SET codigo=?, servico=?, itens=? WHERE id=? AND usuario_id=?",
                        (cod or "FVS", serv[:120], itens, int(d["id"]), uid))
        else:
            if not cod:
                n = con.execute("SELECT COUNT(*) FROM fvs_modelos WHERE usuario_id=?", (uid,)).fetchone()[0]
                cod = f"FVS-{n + 1:02d}"
            con.execute("INSERT INTO fvs_modelos (usuario_id, codigo, servico, itens, criado_em) VALUES (?,?,?,?,?)",
                        (uid, cod, serv[:120], itens, _agora()))


def remover_modelo(uid: int, mid: int) -> None:
    with banco.conectar() as con:
        con.execute("DELETE FROM fvs_modelos WHERE id=? AND usuario_id=?", (mid, uid))


# ------------------------------------------------------------- inspeções
def _proximo_nc(con, uid: int) -> int:
    return (con.execute("SELECT MAX(numero) FROM ncs WHERE usuario_id=?", (uid,)).fetchone()[0] or 0) + 1


def registrar_inspecao(uid: int, d: dict) -> int:
    local = str(d.get("local") or "").strip()
    if not local:
        raise ErroQualidade("Informe o local da inspeção (pavimento, unidade ou área).")
    try:
        data = dt.date.fromisoformat(str(d.get("data"))[:10]).isoformat()
    except ValueError:
        raise ErroQualidade("Informe a data da inspeção.")
    itens_in = d.get("itens") or []
    if not itens_in:
        raise ErroQualidade("A inspeção não tem itens.")
    cid = str(d.get("cid") or "")[:40] or None
    with banco.conectar() as con:
        if cid:
            ja = con.execute("SELECT id FROM inspecoes WHERE usuario_id=? AND cid=?", (uid, cid)).fetchone()
            if ja:
                return ja["id"]
        mod = None
        if d.get("modelo_id"):
            mod = con.execute("SELECT * FROM fvs_modelos WHERE id=? AND usuario_id=?", (int(d["modelo_id"]), uid)).fetchone()
        codigo = (mod["codigo"] if mod else str(d.get("codigo") or "FVS"))
        servico = (mod["servico"] if mod else str(d.get("servico") or "")).strip() or "Serviço"
        itens = []
        for it in itens_in:
            st = it.get("status") if it.get("status") in ("ok", "nc", "na") else None
            if st is None:
                raise ErroQualidade(f"Marque todos os itens (falta: {it.get('item')}).")
            fotos = [f for f in (guardar_foto(con, uid, x) for x in (it.get("fotos") or [])[:4]) if f]
            itens.append({"id": it.get("id"), "item": str(it.get("item") or "")[:160], "criterio": str(it.get("criterio") or "")[:300],
                          "status": st, "obs": str(it.get("obs") or "")[:500], "fotos": fotos})
        reprov = [it for it in itens if it["status"] == "nc"]
        resultado = "reprovada" if reprov else "aprovada"
        assin = guardar_foto(con, uid, d.get("assinatura")) if d.get("assinatura") else None
        reinsp = int(d["reinspecao_de"]) if d.get("reinspecao_de") else None
        cur = con.execute(
            """INSERT INTO inspecoes (usuario_id, cid, modelo_id, codigo, servico, local, data, inspetor, resultado, itens, obs,
               reinspecao_de, assinatura, criado_em) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (uid, cid, mod["id"] if mod else None, codigo, servico, local[:80], data, str(d.get("inspetor") or "")[:80], resultado,
             json.dumps(itens, ensure_ascii=False), str(d.get("obs") or "")[:1000], reinsp, assin, _agora()))
        iid = cur.lastrowid
        prazo = (dt.date.fromisoformat(data) + dt.timedelta(days=7)).isoformat()
        for it in reprov:
            desc = f"{it['item']}: {it['obs']}" if it["obs"] else f"{it['item']} fora do critério ({it['criterio']})"
            con.execute(
                """INSERT INTO ncs (usuario_id, numero, origem, inspecao_id, servico, local, descricao, gravidade, responsavel, prazo,
                   status, fotos, criado_em, atualizado_em) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (uid, _proximo_nc(con, uid), "FVS", iid, servico, local[:80], desc[:600], "media", "", prazo, "aberta",
                 json.dumps(it["fotos"]), _agora(), _agora()))
        # reinspeção aprovada fecha as NCs abertas da inspeção original
        if reinsp and resultado == "aprovada":
            con.execute("""UPDATE ncs SET status='fechada', eficacia=?, fechada_em=?, atualizado_em=?
                           WHERE usuario_id=? AND inspecao_id=? AND status!='fechada'""",
                        (f"Verificada na reinspeção de {data[8:10]}/{data[5:7]}/{data[:4]}", data, _agora(), uid, reinsp))
        return iid


def inspecao(uid: int, iid: int):
    with banco.conectar() as con:
        r = con.execute("SELECT * FROM inspecoes WHERE id=? AND usuario_id=?", (iid, uid)).fetchone()
        if not r:
            return None
        d = dict(r)
        d["itens"] = json.loads(d["itens"])
        d["ncs"] = [dict(x) for x in con.execute("SELECT numero, descricao, status FROM ncs WHERE inspecao_id=? AND usuario_id=?", (iid, uid))]
        return d


def remover_inspecao(uid: int, iid: int) -> None:
    with banco.conectar() as con:
        con.execute("DELETE FROM ncs WHERE inspecao_id=? AND usuario_id=? AND status='aberta'", (iid, uid))
        con.execute("DELETE FROM inspecoes WHERE id=? AND usuario_id=?", (iid, uid))


# ------------------------------------------------------ não conformidades
def salvar_nc(uid: int, d: dict) -> None:
    desc = str(d.get("descricao") or "").strip()
    status = d.get("status") if d.get("status") in ("aberta", "tratamento", "fechada") else "aberta"
    grav = d.get("gravidade") if d.get("gravidade") in ("baixa", "media", "alta") else "media"
    prazo = None
    if d.get("prazo"):
        try:
            prazo = dt.date.fromisoformat(str(d["prazo"])[:10]).isoformat()
        except ValueError:
            prazo = None
    cid = str(d.get("cid") or "")[:40] or None
    with banco.conectar() as con:
        fotos = [f for f in (guardar_foto(con, uid, x) for x in (d.get("fotos") or [])[:6]) if f]
        campos = dict(servico=str(d.get("servico") or "")[:120], local=str(d.get("local") or "")[:80], descricao=desc[:600],
                      gravidade=grav, responsavel=str(d.get("responsavel") or "")[:80], prazo=prazo,
                      causa=str(d.get("causa") or "")[:600], acao=str(d.get("acao") or "")[:600], status=status,
                      eficacia=str(d.get("eficacia") or "")[:300], fotos=json.dumps(fotos))
        if d.get("id"):
            atual = con.execute("SELECT status, fechada_em FROM ncs WHERE id=? AND usuario_id=?", (int(d["id"]), uid)).fetchone()
            if not atual:
                raise ErroQualidade("Não conformidade não encontrada.")
            fechada = atual["fechada_em"] if status == "fechada" and atual["fechada_em"] else (
                dt.date.today().isoformat() if status == "fechada" else None)
            if status == "fechada" and not campos["acao"]:
                raise ErroQualidade("Para fechar, descreva a ação corretiva executada.")
            sets = ", ".join(f"{k}=?" for k in campos)
            con.execute(  # sql-seguro: nomes de coluna fixos no código, valores por parâmetro
                f"UPDATE ncs SET {sets}, fechada_em=?, atualizado_em=? WHERE id=? AND usuario_id=?",
                (*campos.values(), fechada, _agora(), int(d["id"]), uid))
        else:
            if not desc:
                raise ErroQualidade("Descreva a não conformidade.")
            if cid and con.execute("SELECT 1 FROM ncs WHERE usuario_id=? AND cid=?", (uid, cid)).fetchone():
                return
            con.execute(  # sql-seguro: nomes de coluna fixos no código, valores por parâmetro
                f"""INSERT INTO ncs (usuario_id, cid, numero, origem, {', '.join(campos)}, fechada_em, criado_em, atualizado_em)
                    VALUES (?,?,?,?,{','.join('?' * len(campos))},?,?,?)""",
                (uid, cid, _proximo_nc(con, uid), str(d.get("origem") or "Obra")[:30], *campos.values(),
                 dt.date.today().isoformat() if status == "fechada" else None, _agora(), _agora()))


# ------------------------- FVS do cronograma fora da tela (resumo diário): as mesmas regras da tela FVS do cronograma
# (web/index.html: fvsChaves, fvsAuto, qcLocal, mesmoLocal e qcModel). Mudou lá, mude aqui.
FVS_SIN = {"ceram": ["porce", "azule", "piso "], "hidro": ["hidra", "esgot", "sanit", "agua "],
           "esqua": ["janel", "porta", "alumi", "caixi", "vidro"], "forma": ["forma", "armac", "arma "],
           "concr": ["concr"], "gesso": ["forro"], "imper": ["imper"], "pintu": ["textu", "massa"]}
FVS_GEN = {"instalacoes", "revestimento", "servico", "servicos", "execucao"}
_TORRE_RX = re.compile(r"\b(torre|bloco|edificio|predio)(?:\s+|\s*[-–—:]\s*)(norte|sul|leste|oeste|central|(?!e\b)[a-z](?:\d{1,2})?|\d{1,3})\b")
_PAV_RX = [
    (re.compile(r"\b(?:pav(?:to|imento)?\.?\s*)?tipo\s*(\d{1,3})\b"), lambda m: float(m[1])),
    (re.compile(r"\b(\d{1,3})\s*o?\s*(?:pav(?:to|imento)?|andar|pvto)\b\.?"), lambda m: float(m[1])),
    (re.compile(r"\b(?:pav(?:to|imento)?|andar|pvto|piso)\.?\s*(\d{1,3})\b"), lambda m: float(m[1])),
    (re.compile(r"\b(\d{1,3})\s*o?\s*subsolo\b|\bsubsolo\s*(\d{0,2})\b"), lambda m: -(float(m[1] or m[2] or 0) or 1)),
    (re.compile(r"\bterreo\b"), lambda m: 0.0), (re.compile(r"\bmezanino\b"), lambda m: 0.5),
    (re.compile(r"\bcobertura\b"), lambda m: 1000.0), (re.compile(r"\batico\b"), lambda m: 1001.0),
    (re.compile(r"\bbarrilete\b"), lambda m: 1002.0), (re.compile(r"\bcasa de maquinas\b"), lambda m: 1003.0),
]


def _sem_acento(s) -> str:
    import unicodedata
    return "".join(c for c in unicodedata.normalize("NFD", str(s or "")) if not unicodedata.combining(c)).lower()


def _acha_pav(nome: str):
    t = re.sub("º|ª", "o", _sem_acento(nome))
    for rx, f in _PAV_RX:
        m = rx.search(t)
        if m:
            return f(m), m
    return None


def _rot_pav(n: float) -> str:
    if n == 0:
        return "Térreo"
    if n == 0.5:
        return "Mezanino"
    if n < 0:
        return f"Subsolo {int(-n)}"
    return {1000: "Cobertura", 1001: "Ático", 1002: "Barrilete", 1003: "Casa de máquinas"}.get(n, f"{int(n)}º pavimento")


def _subindo(t):
    while t is not None:
        yield t
        t = t.pai


def _torre_de(t) -> str:
    for a in _subindo(t):
        m = _TORRE_RX.search(_sem_acento(a.nome))
        if m:
            w = {"Edificio": "Edifício", "Predio": "Prédio"}.get(m[1].capitalize(), m[1].capitalize())
            return w + " " + (m[2].upper() if len(m[2]) == 1 or m[2][-1].isdigit() else m[2].capitalize())
    return ""


def _pav_de(t):
    for a in _subindo(t):
        r = _acha_pav(a.nome)
        if r:
            return r[0]
    return None


def _serv_de(t) -> str:
    def limpa(nome):
        out, r = nome, _acha_pav(nome)
        if r:
            out = nome[:r[1].start()] + " " + nome[r[1].end():]
        tm = _TORRE_RX.search(_sem_acento(out))
        if tm:
            out = out[:tm.start()] + " " + out[tm.end():]
        out = re.sub(r"\(\s*\)|\[\s*\]", " ", out)
        out = re.sub(r"^[\s\-–—:|/.,]+|[\s\-–—:|/.,(]+$", "", out)
        return re.sub(r"\s{2,}", " ", out).strip()
    for a in _subindo(t):
        n = limpa(a.nome)
        if n:
            return n
    return t.nome


def _fvs_chaves(m) -> list:
    ks: list = []
    for w in re.split(r"\s+", re.sub(r"[^a-z\s]", " ", _sem_acento(m["servico"]))):
        if len(w) < 4:
            continue
        p = w[:5]
        if p not in ks:
            ks.append(p)
        for s in FVS_SIN.get(p, []):
            if s not in ks:
                ks.append(s)
        if w in FVS_GEN and p in ks:
            ks[ks.index(p)] = {"fraca": p}
    return ks


def _fvs_auto(t, modelos: list):
    nomes = [_serv_de(t) + " " + t.nome] + [a.nome for a in _subindo(t.pai)]
    for nome in nomes:
        txt = " " + re.sub(r"\s+", " ", re.sub(r"[^a-z\s]", " ", _sem_acento(nome))) + " "
        tem = lambda p: (" " + p.strip()) in txt  # noqa: E731
        best, bs = None, 0
        for m in modelos:
            s, forte = 0, False
            for k in _fvs_chaves(m):
                if isinstance(k, str):
                    if tem(k):
                        s += 2
                        forte = True
                elif tem(k["fraca"]):
                    s += 1
            if forte and s > bs:
                bs, best = s, m
        if best:
            return best
    return None


def _mesmo_local(local: str, rotulo: str, torre: str, pav) -> bool:
    if local == rotulo:
        return True
    if pav is None:
        return False
    r = _acha_pav(local)
    if not r or r[0] != pav:
        return False
    return not torre or not _TORRE_RX.search(_sem_acento(local)) or _sem_acento(torre) in _sem_acento(local)


def fvs_atrasadas(uid: int, projeto, hoje: dt.date) -> int | None:
    """FVS do cronograma com o prazo de inspeção vencido (término da tarefa + 2 dias) e sem inspeção aprovada,
    como na tela FVS do cronograma. None quando a obra não tem cronograma ou modelos de FVS."""
    if projeto is None:
        return None
    with banco.conectar() as con:
        modelos = [dict(r) for r in con.execute("SELECT id, servico FROM fvs_modelos WHERE usuario_id=? ORDER BY codigo, id", (uid,))]
        insp = [dict(r) for r in con.execute("SELECT id, modelo_id, local, data, resultado FROM inspecoes WHERE usuario_id=?", (uid,))]
    if not modelos:
        return None
    insp.sort(key=lambda i: (i["data"] or "") + str(i["id"]))
    lig = tarefas(uid)
    por_id = {m["id"]: m for m in modelos}
    agora = dt.datetime.combine(hoje, dt.time())
    n = 0
    for t in projeto.tarefas:
        if t.filhos or t.marco or t.inicio is None or t.termino is None:
            continue
        escolha, auto = lig.get(t.uid), _fvs_auto(t, modelos)
        if escolha and escolha.get("modelo_id") == 0:
            continue
        m = por_id.get(escolha["modelo_id"], auto) if escolha and escolha.get("modelo_id") else auto
        if not m:
            continue
        torre, pav = _torre_de(t), _pav_de(t)
        loc = _rot_pav(pav) if pav is not None else ""
        rotulo = f"{torre} · {loc}" if torre and loc else (torre or loc or t.nome)
        ult = None
        for i in insp:
            if i["modelo_id"] == m["id"] and _mesmo_local(i["local"] or "", rotulo, torre, pav):
                ult = i
        ate = t.termino + dt.timedelta(days=2)
        dias = math.floor((agora - ate).total_seconds() / 86400 + 0.5)  # Math.round do JavaScript
        if dias > 0 and not (ult and ult["resultado"] == "aprovada"):
            n += 1
    return n
