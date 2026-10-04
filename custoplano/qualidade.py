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
import re
import secrets

import banco

ESQUEMA = """
CREATE TABLE IF NOT EXISTS fvs_modelos (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    usuario_id  INTEGER NOT NULL REFERENCES usuarios(id) ON DELETE CASCADE,
    codigo      TEXT NOT NULL,
    servico     TEXT NOT NULL,
    itens       TEXT NOT NULL,              -- JSON [{id, item, criterio}]
    criado_em   TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS inspecoes (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    usuario_id    INTEGER NOT NULL REFERENCES usuarios(id) ON DELETE CASCADE,
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
    usuario_id   INTEGER NOT NULL REFERENCES usuarios(id) ON DELETE CASCADE,
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
    usuario_id  INTEGER NOT NULL REFERENCES usuarios(id) ON DELETE CASCADE,
    mime        TEXT NOT NULL,
    dados       BLOB NOT NULL,
    criado_em   TEXT NOT NULL
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
    return {"modelos": modelos, "inspecoes": insp, "ncs": ncs, "locais": locais, "hoje": dt.date.today().isoformat()}


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
            con.execute(f"UPDATE ncs SET {sets}, fechada_em=?, atualizado_em=? WHERE id=? AND usuario_id=?",
                        (*campos.values(), fechada, _agora(), int(d["id"]), uid))
        else:
            if not desc:
                raise ErroQualidade("Descreva a não conformidade.")
            if cid and con.execute("SELECT 1 FROM ncs WHERE usuario_id=? AND cid=?", (uid, cid)).fetchone():
                return
            con.execute(
                f"""INSERT INTO ncs (usuario_id, cid, numero, origem, {', '.join(campos)}, fechada_em, criado_em, atualizado_em)
                    VALUES (?,?,?,?,{','.join('?' * len(campos))},?,?,?)""",
                (uid, cid, _proximo_nc(con, uid), str(d.get("origem") or "Obra")[:30], *campos.values(),
                 dt.date.today().isoformat() if status == "fechada" else None, _agora(), _agora()))
