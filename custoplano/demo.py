"""Obra de demonstração: monta um cronograma de exemplo com as datas em torno de hoje e preenche
a conta demo com medição, histórico, curto prazo, suprimentos e custos. Assim a demonstração usa
exatamente as mesmas telas da área do cliente."""
from __future__ import annotations

import datetime as dt
import json
import random
from xml.sax.saxutils import escape

import banco
from cronograma import Projeto

TITULO = "Residencial Aurora"
ARQUIVO = "Residencial Aurora.xml"
PAVS = 8
# serviço, defasagem em semanas a partir da estrutura do mesmo pavimento, custo por pavimento (R$), fornecedor
SERVICOS = [
    ("Estrutura", 0, 186000, "Concreta Estruturas", 30),
    ("Alvenaria", 3, 64000, "Cerâmica Vale", 15),
    ("Instalações", 5, 52000, "Hidro Center", 20),
    ("Reboco", 7, 38000, "Argamassa Forte", 10),
    ("Contrapiso", 9, 26000, "Base Pisos", 10),
    ("Revestimento", 11, 71000, "Revest Sul", 25),
    ("Pintura", 13, 33000, "Cor & Arte Pinturas", 10),
]
TORRES = [("Torre A", 0), ("Torre B", 2)]  # nome, semanas de defasagem
SEMANAS_ATE_HOJE = 14


def _seg(d: dt.date) -> dt.date:
    return d - dt.timedelta(days=d.weekday())


def _atraso(torre: str, si: int, pav: int) -> int:
    """Dias de atraso do cronograma atual contra a linha de base (para o exemplo ter desvio)."""
    if torre == "Torre A" and si >= 1 and pav >= 5:
        return 7
    if torre == "Torre B" and si >= 2 and pav >= 3:
        return 7
    return 0


def _tarefas(hoje: dt.date):
    """Lista de tarefas (dicts) com datas de linha de base e atuais."""
    w0 = _seg(hoje) - dt.timedelta(weeks=SEMANAS_ATE_HOJE)
    sem = lambda n: w0 + dt.timedelta(weeks=n)  # noqa: E731
    out = []

    def folha(nome, eap, nivel, ini_b, dias, atraso=0, custo=0, pred=None):
        fim_b = ini_b + dt.timedelta(days=dias - 1 + (dias - 1) // 5 * 2)
        out.append(dict(nome=nome, eap=eap, nivel=nivel, resumo=False, ini_b=ini_b, fim_b=fim_b,
                        ini=ini_b + dt.timedelta(days=atraso), fim=fim_b + dt.timedelta(days=atraso),
                        dias=dias, custo=custo, pred=pred or []))
        return out[-1]

    def resumo(nome, eap, nivel):
        out.append(dict(nome=nome, eap=eap, nivel=nivel, resumo=True, pred=[]))
        return out[-1]

    resumo(TITULO, "1", 1)
    resumo("Serviços preliminares", "1.1", 2)
    folha("Canteiro de obras", "1.1.1", 3, sem(0), 5, custo=48000)
    folha("Fundações", "1.1.2", 3, sem(1), 15, custo=320000)
    for ti, (torre, lag) in enumerate(TORRES):
        resumo(torre, f"1.{ti + 2}", 2)
        for si, (serv, slag, custo, _f, _l) in enumerate(SERVICOS):
            resumo(serv, f"1.{ti + 2}.{si + 1}", 3)
            for pav in range(1, PAVS + 1):
                folha(f"{pav}º pavimento", f"1.{ti + 2}.{si + 1}.{pav}", 4, sem(4 + lag + slag + pav - 1), 5,
                      atraso=_atraso(torre, si, pav), custo=custo)
    fim = max(t["fim"] for t in out if not t["resumo"])
    fim_b = max(t["fim_b"] for t in out if not t["resumo"])
    out.append(dict(nome="Entrega da obra", eap="1.4", nivel=2, resumo=False, marco=True, ini_b=fim_b, fim_b=fim_b,
                    ini=fim, fim=fim, dias=0, custo=0, pred=[]))
    # datas dos resumos a partir das folhas
    for i, t in enumerate(out):
        t["uid"] = i + 1
        if t["resumo"]:
            filhos = [f for f in out if not f["resumo"] and f["eap"].startswith(t["eap"] + ".")]
            for k in ("ini", "fim", "ini_b", "fim_b"):
                t[k] = (min if k.startswith("ini") else max)(f[k] for f in filhos)
    return out


def _xml(tarefas, hoje: dt.date) -> str:
    def dd(d, h):
        return f"{d.isoformat()}T{h}"
    partes = []
    for t in tarefas:
        marco = t.get("marco", False)
        dur = 0 if (t["resumo"] or marco) else t["dias"] * 8
        custo = "" if t["resumo"] or not t["custo"] else f"<Cost>{t['custo'] * 100}</Cost>"
        custo_lb = "" if t["resumo"] or not t["custo"] else f"<Cost>{t['custo'] * 100}</Cost>"
        partes.append(
            f"<Task><UID>{t['uid']}</UID><ID>{t['uid']}</ID><Name>{escape(t['nome'])}</Name><WBS>{t['eap']}</WBS>"
            f"<OutlineNumber>{t['eap']}</OutlineNumber><OutlineLevel>{t['nivel']}</OutlineLevel>"
            f"<Start>{dd(t['ini'], '08:00:00')}</Start><Finish>{dd(t['fim'], '17:00:00')}</Finish>"
            f"<Duration>PT{dur}H0M0S</Duration><Milestone>{1 if marco else 0}</Milestone><Summary>{1 if t['resumo'] else 0}</Summary>"
            f"<PercentComplete>0</PercentComplete><PhysicalPercentComplete>0</PhysicalPercentComplete>{custo}"
            f"<Baseline><Number>0</Number><Start>{dd(t['ini_b'], '08:00:00')}</Start><Finish>{dd(t['fim_b'], '17:00:00')}</Finish>{custo_lb}</Baseline></Task>"
        )
    return ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Project xmlns="http://schemas.microsoft.com/project">'
            f"<SaveVersion>14</SaveVersion><Name>{ARQUIVO}</Name><Title>{TITULO}</Title><MinutesPerDay>480</MinutesPerDay>"
            f"<StatusDate>{hoje.isoformat()}T17:00:00</StatusDate><CurrentDate>{hoje.isoformat()}T08:00:00</CurrentDate>"
            f"<CurrencySymbol>R$</CurrencySymbol><Calendars/><Tasks>{''.join(partes)}</Tasks></Project>")


def _avanco(t, d: dt.date) -> float:
    """% físico de uma folha numa data, pelo cronograma atual (andamento linear, de 5 em 5%)."""
    if d >= t["fim"]:
        return 100.0
    if d < t["ini"]:
        return 0.0
    total = (t["fim"] - t["ini"]).days + 1
    return float(min(95, max(5, round(((d - t["ini"]).days + 1) / total * 20) * 5)))


def _medicao(tarefas, d: dt.date) -> dict:
    return {str(t["uid"]): _avanco(t, d) for t in tarefas if not t["resumo"]}


def _semanas_mes(a: dt.date, b: dt.date):
    """Semanas do mês (segunda a domingo, cortadas na virada do mês) entre a e b, como no curto prazo."""
    d = a
    while d < b:
        seg = _seg(d)
        inicio = max(seg, d.replace(day=1))
        prox_mes = (d.replace(day=28) + dt.timedelta(days=4)).replace(day=1)
        fim = min(seg + dt.timedelta(days=7), prox_mes)
        yield inicio, fim
        d = fim


def semear(uid: int, hoje: dt.date | None = None) -> None:
    """Apaga o que houver na conta demo e grava a obra de demonstração com as datas de hoje."""
    hoje = hoje or dt.date.today()
    if hoje.weekday() >= 5:  # data de status sempre num dia útil (sexta-feira no fim de semana)
        hoje -= dt.timedelta(days=hoje.weekday() - 4)
    rnd = random.Random(7)
    tarefas = _tarefas(hoje)
    xml = _xml(tarefas, hoje)
    p = Projeto(xml, ARQUIVO)
    folhas = [t for t in tarefas if not t["resumo"]]
    porid = {t["uid"]: t for t in tarefas}

    banco.remover_cronograma(uid)
    banco.remover_custos(uid)
    banco.remover_plano(uid)
    with banco.conectar() as con:
        con.execute("DELETE FROM medicoes_fechadas WHERE usuario_id=?", (uid,))

    # medições fechadas a cada duas semanas (sexta-feira), da primeira semana de estrutura até a semana passada
    sexta = _seg(hoje) - dt.timedelta(days=3)
    datas = [sexta - dt.timedelta(weeks=2 * k) for k in range(7, 0, -1)] + [sexta]
    anterior = {}
    hist = []
    for d in datas:
        med = _medicao(tarefas, d)
        sd = dt.datetime.combine(d, dt.time(17))
        A = p.agregar(med, anterior, sd, "dur")
        hist.append((d, p.pct(A["total"], "r"), p.pct(A["total"], "p"), med, anterior))
        anterior = med
    with banco.conectar() as con:
        for d, real, prev, med, ant in hist:
            con.execute(
                """INSERT INTO medicoes_fechadas (usuario_id, arquivo, data_status, fechado_em, avanco_real, avanco_prev, peso, xml, atual, anterior)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (uid, ARQUIVO, d.isoformat(), dt.datetime.combine(d, dt.time(18)).isoformat(timespec="seconds"),
                 real, prev, "dur", xml, json.dumps(med), json.dumps(ant)))

    # medição atual (hoje) e a anterior (última fechada)
    atual = _medicao(tarefas, hoje)
    # duas tarefas que já deviam ter terminado ficam incompletas, para o painel mostrar atraso real
    presas = sorted([t for t in folhas if not t.get("marco") and t["fim_b"] < hoje - dt.timedelta(days=2)],
                    key=lambda t: t["fim_b"], reverse=True)[:2]
    for t, v in zip(presas, (70.0, 85.0)):
        atual[str(t["uid"])] = min(atual[str(t["uid"])], v)
    banco.salvar_cronograma(uid, ARQUIVO, xml, "dur", hoje.isoformat(), atual)
    banco.salvar_medicao(uid, atual, "dur", hoje.isoformat(), hist[-1][3])

    # suprimentos: um pacote por serviço de cada torre (linha resumo)
    sup = {}
    for t in tarefas:
        if not t["resumo"] or t["nivel"] != 3 or t["eap"].startswith("1.1"):
            continue
        serv = next(s for s in SERVICOS if s[0] == t["nome"])
        lead = serv[4]
        comprar = t["ini"] - dt.timedelta(days=lead)
        if t["ini"] <= hoje:
            st = "entregue"
        elif comprar <= hoje:
            st = "pedido" if rnd.random() < 0.6 else "cotacao"
        elif comprar <= hoje + dt.timedelta(days=21):
            st = "cotacao"
        else:
            st = "solicitar"
        sup[str(t["uid"])] = {"lead": lead, "forn": serv[3], "st": st}
    # um pacote com a compra atrasada, para o exemplo mostrar o alerta
    atrasaveis = [k for k, v in sup.items() if v["st"] in ("pedido", "cotacao")]
    if atrasaveis:
        sup[atrasaveis[0]]["st"] = "solicitar"
    banco.salvar_suprimentos(uid, sup)

    # curto prazo: apontamentos das últimas semanas, com PPC entre 60% e 90%
    cp = {}
    causas = ["Material", "Mão de obra", "Predecessora", "Clima", "Projeto", "Equipamento"]
    inicio = _seg(hoje) - dt.timedelta(weeks=8)
    for a, b in _semanas_mes(inicio, _seg(hoje)):
        lista = [t for t in folhas if not t.get("marco") and t["ini"] < b and t["fim"] >= a]
        if not lista:
            continue
        semana = {}
        for t in lista:
            if rnd.random() < 0.78:
                semana[str(t["uid"])] = {"st": "ok", "causa": ""}
            else:
                semana[str(t["uid"])] = {"st": "nok", "causa": rnd.choice(causas)}
        cp[a.isoformat()] = semana
    # semana atual: só o que já terminou até a data de status está apontado
    for a, b in _semanas_mes(_seg(hoje), _seg(hoje) + dt.timedelta(days=7)):
        if a <= hoje < b:
            semana = {str(t["uid"]): {"st": "ok", "causa": ""} for t in folhas
                      if not t.get("marco") and t["ini"] < b and t["fim"] >= a and t["fim"] <= hoje}
            for t, causa in zip(presas, ("Material", "Mão de obra")):
                if str(t["uid"]) in semana or (t["ini"] < b and t["fim"] >= a):
                    semana[str(t["uid"])] = {"st": "nok", "causa": causa}
            if semana:
                cp[a.isoformat()] = semana
    banco.salvar_curto_prazo(uid, cp)

    # custos: orçamento vem das colunas de custo do cronograma; realizado lançado pelo % físico
    lanc = []
    fator = {"Estrutura": 1.06, "Alvenaria": 0.98, "Instalações": 1.11, "Reboco": 1.02}
    for t in folhas:
        if not t["custo"]:
            continue
        pct = atual.get(str(t["uid"]), 0)
        if pct <= 0:
            continue
        pai = next((porid[x["uid"]] for x in tarefas if x["resumo"] and t["eap"].startswith(x["eap"] + ".") and x["nivel"] == 3), None)
        serv = pai["nome"] if pai and pai["eap"] != "1.1" else t["nome"]
        forn = next((s[3] for s in SERVICOS if s[0] == serv), "Equipe própria")
        v = round(t["custo"] * pct / 100 * fator.get(serv, 1.0), 2)
        quando = min(t["fim"], hoje)
        lanc.append({"id": f"demo{t['uid']}", "data": quando.isoformat(), "codigo": t["eap"],
                     "descricao": f"{serv} · {t['nome']}" if serv != t["nome"] else t["nome"], "fornecedor": forn,
                     "tipo": "out", "valor": v, "origem": "sistema", "criado_em": dt.datetime.now().isoformat(timespec="seconds")})
    import custos as cst
    dados = cst.dados_do_projeto(p)
    if dados:
        banco.salvar_orcamento(uid, ARQUIVO, dados)
        banco.salvar_lancamentos(uid, lanc)
