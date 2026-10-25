"""
Relatório mensal de avanço em PDF, no padrão ABNT (NBR 10719): capa, folha de rosto, resumo, sumário,
introdução, prazo (Curva S, avanço por etapa, maiores atrasos, plano de ataque), custo, qualidade, conclusão
e referências.

Usa só as fontes padrão do PDF (Helvetica), que funcionam no servidor sem instalar nada.
"""
from __future__ import annotations

import datetime as dt
import io
import os

from cronograma import Projeto

MESES_EXT = ["janeiro", "fevereiro", "março", "abril", "maio", "junho", "julho", "agosto", "setembro",
             "outubro", "novembro", "dezembro"]
MESES = ["jan", "fev", "mar", "abr", "mai", "jun", "jul", "ago", "set", "out", "nov", "dez"]


def _br(v: float, casas: int = 1) -> str:
    return f"{v:.{casas}f}".replace(".", ",")


def _d(x) -> str:
    if x is None:
        return "-"
    if isinstance(x, str):
        try:
            x = dt.date.fromisoformat(x[:10])
        except ValueError:
            return x
    return x.strftime("%d/%m/%Y")


def _dias(n) -> str:
    if n is None:
        return "-"
    return ("+" if n > 0 else "") + f"{n} {'dia' if abs(n) == 1 else 'dias'}"


def curva_prevista(p: Projeto, criterio: str | None, fim_real: dt.datetime) -> list[tuple[dt.datetime, float]]:
    """Avanço previsto pela linha de base, um ponto por mês."""
    datas = [t.lb_inicio or t.inicio for t in p.tarefas] + [t.lb_termino or t.termino for t in p.tarefas]
    datas = [d for d in datas if d]
    if not datas:
        return []
    ini, fim = min(datas), max(max(datas), fim_real)
    pontos, d = [], dt.datetime(ini.year, ini.month, 1, 17)
    while True:
        tot = p.agregar({}, {}, d, criterio)["total"]
        pontos.append((d, p.pct(tot, "p")))
        if d > fim:
            break
        d = dt.datetime(d.year + (d.month // 12), d.month % 12 + 1, 1, 17)
    return pontos


def resumo_plano(dados: dict) -> dict:
    db = dt.date.fromisoformat(dados["data_base"]) if dados.get("data_base") else None
    tot = ok = late = prev = prev_ok = 0
    for pav in dados["atual"]["pavimentos"]:
        for i, s in enumerate(pav["datas"]):
            if not s:
                continue
            d = dt.date.fromisoformat(s)
            feito = bool(pav["ok"] and pav["ok"][i])
            tot += 1
            ok += feito
            if db and d <= db:
                prev += 1
                prev_ok += feito
                late += not feito
    return {"tot": tot, "ok": ok, "late": late, "prev": prev, "prev_ok": prev_ok, "db": db}


# Relatório de cada tela do sistema: título, subtítulo e gerência interna que escreve e revisa o texto
# (a gerência não aparece no documento).
# O consolidado (Painel) reúne prazo, custo e qualidade.
TIPOS = {
    "consolidado": ("RELATÓRIO MENSAL DE AVANÇO", "prazo, custo e qualidade",
                    "o acompanhamento de prazo, custo e qualidade", "Gerências de planejamento, de orçamento e de qualidade"),
    "prazo": ("RELATÓRIO DE PRAZO", "cronograma e avanço físico", "o cronograma e o avanço físico", "Gerência de planejamento"),
    "medicao": ("RELATÓRIO DE MEDIÇÃO", "avanço físico por tarefa", "o avanço físico, tarefa por tarefa,", "Gerência de planejamento"),
    "plano": ("RELATÓRIO DO PLANO DE ATAQUE", "serviços por pavimento", "a execução do plano de ataque, com os serviços por pavimento,",
              "Gerência de planejamento"),
    "curto": ("RELATÓRIO DE PLANEJAMENTO DE CURTO PRAZO", "pacotes semanais e PPC", "os pacotes semanais e o PPC",
              "Gerência de planejamento"),
    "custos": ("RELATÓRIO DE CUSTOS", "orçado, agregado e realizado", "os valores orçado, agregado e realizado",
               "Gerência de orçamento"),
    "suprimentos": ("RELATÓRIO DE SUPRIMENTOS", "pacotes de compra e prazos", "os pacotes de compra e os prazos de cada um",
                    "Gerência de orçamento"),
    "qualidade": ("RELATÓRIO DE QUALIDADE", "inspeções e não conformidades", "as inspeções e as não conformidades",
                  "Gerência de qualidade"),
    "inspecoes": ("RELATÓRIO DE INSPEÇÕES", "fichas de verificação de serviço (FVS)",
                  "as fichas de verificação de serviço (FVS) registradas", "Gerência de qualidade"),
    "ncs": ("RELATÓRIO DE NÃO CONFORMIDADES", "registro, tratamento e prazos",
            "o registro, o tratamento e os prazos das não conformidades", "Gerência de qualidade"),
    "modelos": ("RELATÓRIO DOS MODELOS DE FVS", "itens de verificação e critérios de aceitação",
                "os itens de verificação e os critérios de aceitação dos modelos de FVS", "Gerência de qualidade"),
}
GRAVIDADE = {"alta": "Alta", "media": "Média", "baixa": "Baixa"}
SITUACAO_NC = {"aberta": "Aberta", "tratamento": "Em tratamento", "fechada": "Fechada"}
ETAPA_SUP = {"solicitar": "A solicitar", "cotacao": "Em cotação", "pedido": "Pedido feito", "entregue": "Entregue"}


def _plural(n: int, um: str, varios: str) -> str:
    return f"{n} {um if n == 1 else varios}"


def _sn(v: float, casas: int = 1) -> str:
    """Número com sinal: +1,2 ou -1,2; zero sai sem sinal (0,0)."""
    txt = _br(abs(v), casas)
    if txt.strip("0,") == "":
        return txt
    return ("+" if v > 0 else "-") + txt


def suprimentos_modelo(p: Projeto, A: dict, sup: dict, hoje: dt.date) -> list[dict]:
    """Os mesmos pacotes de compra da tela Suprimentos: cada linha resumo do cronograma, com a data de
    necessidade (início), o prazo do fornecedor e a data-limite de compra."""
    raizes = [t for t in p.tarefas if t.pai is None]
    out = []
    for t in p.tarefas:
        if not t.filhos or t.inicio is None or (len(raizes) == 1 and t is raizes[0]):
            continue
        sv = sup.get(t.uid) or {}
        lead = sv.get("lead") if sv.get("lead") is not None else 15
        st = sv.get("st") or "solicitar"
        need = t.inicio.date()
        buy = need - dt.timedelta(days=lead)
        dias = (buy - hoje).days
        feito = p.pct(A[t.uid], "r") >= 99.5
        if st == "entregue":
            sit, grupo = "Entregue", "ok"
        elif feito:
            sit, grupo = "Pacote concluído", "ok"
        elif st == "pedido":
            sit, grupo = ("Entrega prevista na semana" if need < hoje + dt.timedelta(days=7) else "Aguardando entrega"), "ok"
        elif dias < 0:
            sit, grupo = f"{_plural(-dias, 'dia', 'dias')} de atraso", "late"
        elif dias <= 7:
            sit, grupo = f"Comprar até {buy:%d/%m}", "week"
        else:
            sit, grupo = "No prazo", "ok"
        nome = f"{t.pai.nome} · {t.nome}" if t.pai is not None and not (len(raizes) == 1 and t.pai is raizes[0]) else t.nome
        out.append({"t": t, "nome": nome, "lead": lead, "forn": sv.get("forn") or "", "st": st, "need": need, "buy": buy,
                    "sit": sit, "grupo": grupo, "dias": dias})
    return out


def gerar(p: Projeto | None, atual: dict, anterior: dict, data_status: dt.datetime, criterio: str | None,
          historico: list, plano: dict | None, obra: str, custos: dict | None = None,
          indicadores: dict | None = None, tipo: str = "consolidado", extra: dict | None = None) -> bytes:
    """Relatório no padrão ABNT (NBR 10719): capa, folha de rosto, resumo, sumário, introdução,
    desenvolvimento, conclusão e referências. `tipo` escolhe o assunto (uma chave de TIPOS); o consolidado
    traz prazo, custo e qualidade juntos. `extra` traz os dados das telas específicas: qualidade (estado
    completo), curto (apontamentos semanais) e sup (suprimentos)."""
    from reportlab.graphics.shapes import Circle, Drawing, Line, PolyLine, Rect, String
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY, TA_LEFT
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import cm
    from reportlab.platypus import (BaseDocTemplate, Frame, Image, KeepTogether, NextPageTemplate, PageBreak,
                                    PageTemplate, Paragraph, Spacer, Table, TableStyle)
    from reportlab.platypus.tableofcontents import TableOfContents

    tipo = tipo if tipo in TIPOS else "consolidado"
    titulo, subtitulo, assunto, _gerencia = TIPOS[tipo]
    cons = tipo == "consolidado"
    extra = extra or {}
    # cores padrão da Custo Plano (projetos/_modelo/marca/cores-padrao.md)
    NAVY, NAVY2, CLARO = colors.HexColor("#002455"), colors.HexColor("#261FB3"), colors.HexColor("#ECF0F1")
    PRETO, CINZA, LINHA = colors.black, colors.HexColor("#555555"), colors.HexColor("#B8C2CC")
    ind = indicadores or {}
    qual = ind.get("qualidade") or {}
    QE = extra.get("qualidade") or {}

    # Arial 12 no texto (Helvetica é a mesma métrica e já vem no PDF), espaçamento 1,5, recuo de 1,25 cm
    def est(nome, **k):
        base = dict(fontName="Helvetica", fontSize=12, leading=18, textColor=PRETO)
        base.update(k)
        return ParagraphStyle(nome, **base)

    S = {
        "txt": est("txt", alignment=TA_JUSTIFY, firstLineIndent=1.25 * cm),
        "resumo": est("resumo", alignment=TA_JUSTIFY, leading=14),
        "centro": est("centro", alignment=TA_CENTER),
        "centrob": est("centrob", alignment=TA_CENTER, fontName="Helvetica-Bold"),
        "natureza": est("natureza", fontSize=10, leading=12, leftIndent=8 * cm, alignment=TA_JUSTIFY),
        "h1": est("h1", fontName="Helvetica-Bold", spaceBefore=18, spaceAfter=12, keepWithNext=1),
        "h2": est("h2", fontName="Helvetica-Bold", spaceBefore=12, spaceAfter=12, keepWithNext=1),
        "tsn": est("tsn", fontName="Helvetica-Bold", alignment=TA_CENTER, spaceAfter=18),
        "tsnref": est("tsnref", fontName="Helvetica-Bold", alignment=TA_CENTER, spaceAfter=18),
        "item": est("item", alignment=TA_JUSTIFY, leftIndent=0.6 * cm, firstLineIndent=-0.6 * cm),
        "leg": est("leg", fontSize=10, leading=12, alignment=TA_CENTER, spaceBefore=6, spaceAfter=3),
        "fonte": est("fonte", fontSize=10, leading=12, alignment=TA_LEFT, spaceBefore=3, spaceAfter=12),
        "ref": est("ref", leading=14, alignment=TA_LEFT, spaceAfter=12),
        "t": est("t", fontSize=10, leading=12),
        "tr": est("tr", fontSize=10, leading=12, alignment=2),
        "th": est("th", fontName="Helvetica-Bold", fontSize=10, leading=12, textColor=colors.white),
        "thr": est("thr", fontName="Helvetica-Bold", fontSize=10, leading=12, textColor=colors.white, alignment=2),
        "small": est("small", fontSize=10, leading=12, textColor=CINZA),
    }
    esc = lambda s: str(s if s is not None else "").replace("&", "&amp;").replace("<", "&lt;")  # noqa: E731
    Pa = lambda t, s="t": Paragraph(t, S[s])  # noqa: E731

    n_raizes = sum(1 for x in p.tarefas if x.pai is None) if p else 0

    def nome_t(t) -> str:
        """Nome da tarefa com as linhas de resumo acima dela (sem a linha da obra inteira)."""
        partes = [t.nome]
        pai = t.pai
        while pai is not None:
            if pai.pai is not None or n_raizes > 1:
                partes.append(pai.nome)
            pai = pai.pai
        return " · ".join(reversed(partes))
    cor_desv = lambda v, lim=-0.05: "#8E1616" if v < lim else "#002455"  # noqa: E731
    destaque = lambda txt, ruim: Pa(f"<font color='{'#8E1616' if ruim else '#002455'}'><b>{txt}</b></font>", "tr")  # noqa: E731
    ntab, nfig, nsec = [0], [0], [0]

    def legenda(tipo_, texto):
        cont = ntab if tipo_ == "Tabela" else nfig
        cont[0] += 1
        return Pa(f"{tipo_} {cont[0]} – {texto}", "leg")

    def fonte_dados(texto="Custo Plano Consultoria"):
        return Pa(f"Fonte: {texto} ({data_status.year}).", "fonte")

    def tabela(titulo_, cab, linhas, larg, direita=(), total=False, fonte="Custo Plano Consultoria"):
        """Tabela aberta nas laterais (padrão IBGE), cabeçalho azul-marinho, legenda acima e fonte abaixo."""
        dados = [[Pa(h, "thr" if i in direita else "th") for i, h in enumerate(cab)]]
        for ln in linhas:
            dados.append([c if not isinstance(c, str) else Pa(esc(c), "tr" if i in direita else "t") for i, c in enumerate(ln)])
        t = Table(dados, colWidths=larg, repeatRows=1)
        estilo = [("BACKGROUND", (0, 0), (-1, 0), NAVY), ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                  ("LINEABOVE", (0, 0), (-1, 0), 0.8, NAVY), ("LINEBELOW", (0, -1), (-1, -1), 0.8, NAVY),
                  ("LINEBELOW", (0, 1), (-1, -2), 0.3, LINHA),
                  ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                  ("LEFTPADDING", (0, 0), (-1, -1), 4), ("RIGHTPADDING", (0, 0), (-1, -1), 4)]
        if total:
            estilo.append(("BACKGROUND", (0, -1), (-1, -1), CLARO))
        t.setStyle(TableStyle(estilo))
        bloco = [legenda("Tabela", titulo_), t, fonte_dados(fonte)]
        return KeepTogether(bloco) if len(dados) <= 20 else bloco

    def add(x):
        H.extend(x) if isinstance(x, list) else H.append(x)

    def txt(t):
        H.append(Pa(t, "txt"))

    def secao(nome):
        """Seção secundária numerada (2.1, 2.2...) dentro do desenvolvimento."""
        nsec[0] += 1
        H.append(Pa(f"2.{nsec[0]} {nome}", "h2"))

    def prox():
        return f"Tabela {ntab[0] + 1}"

    ref = data_status if p else dt.datetime.combine((plano and resumo_plano(plano)["db"]) or dt.date.today(), dt.time(17))
    mes_ano = f"{MESES_EXT[ref.month - 1]} de {ref.year}"
    gerado = dt.datetime.now()
    hoje = ref.date()  # prazos vencidos, compras e janelas de 30 dias contam até a data de status

    # ------------------------------------------------------------------ páginas
    class Doc(BaseDocTemplate):
        def afterFlowable(self, f):
            if isinstance(f, Paragraph) and f.style.name in ("h1", "h2", "tsnref"):
                nivel = 1 if f.style.name == "h2" else 0
                self.notify("TOCEntry", (nivel, f.getPlainText(), self.page - 1))

    def pg_texto(c, doc):
        w, h = A4
        c.saveState()
        c.setFont("Helvetica", 10)
        c.setFillColor(PRETO)
        c.drawRightString(w - 2 * cm, h - 2 * cm, str(doc.page - 1))  # a capa não conta; a folha de rosto é a 1
        c.setFillColor(CINZA)
        c.setFont("Helvetica", 8)
        c.drawString(3 * cm, 1.2 * cm, f"Gerado em {gerado:%d/%m/%Y}, às {gerado:%Hh%M}, pelo sistema Custo Plano.")
        c.restoreState()

    buf = io.BytesIO()
    # margens ABNT: superior e esquerda 3 cm, inferior e direita 2 cm
    doc = Doc(buf, pagesize=A4, leftMargin=3 * cm, rightMargin=2 * cm, topMargin=3 * cm, bottomMargin=2 * cm,
              title=f"{titulo.capitalize()} - {obra}", author="Custo Plano Consultoria")
    fr = lambda: Frame(doc.leftMargin, doc.bottomMargin, doc.width, doc.height, id="f")  # noqa: E731
    doc.addPageTemplates([PageTemplate(id="pre", frames=[fr()]),
                          PageTemplate(id="texto", frames=[fr()], onPage=pg_texto)])
    W = doc.width
    H = []

    # ------------------------------------------------------------- números
    A = tot = None
    real = prev = ant = desv = None
    if p:
        A = p.agregar(atual, anterior, data_status, criterio)
        tot = A["total"]
        real, prev, ant = round(p.pct(tot, "r"), 1), round(p.pct(tot, "p"), 1), round(p.pct(tot, "b"), 1)
        desv = round(real - prev, 1)
    T = (custos or {}).get("totais") or {}
    idc, idp = T.get("idc"), T.get("idp")
    ppc = ind.get("ppc")
    brl = lambda v: "-" if v is None else ("-" if v < -0.5 else "") + "R$ " + f"{abs(v):,.0f}".replace(",", ".")  # noqa: E731
    # tabelas com várias colunas em R$ mil, para os valores caberem numa linha só
    mil = lambda v: "-" if v is None else f"{v / 1000:,.0f}".replace(",", ".")  # noqa: E731
    rp = resumo_plano(plano) if plano else None
    sup = suprimentos_modelo(p, A, extra.get("sup") or {}, hoje) if (p and tipo == "suprimentos") else []
    curto = extra.get("curto") or {}
    semanas = sorted(curto)[-12:]
    ppc_sem = []
    for s_ in semanas:
        ok = sum(1 for a in curto[s_].values() if a.get("st") == "ok")
        nok = sum(1 for a in curto[s_].values() if a.get("st") == "nok")
        if ok + nok:
            ppc_sem.append((s_, ok, nok, round(100 * ok / (ok + nok))))
    causas: dict[str, int] = {}
    for s_ in semanas:
        for a in curto[s_].values():
            if a.get("st") == "nok":
                c_ = a.get("causa") or "Sem causa registrada"
                causas[c_] = causas.get(c_, 0) + 1

    def frase_causas() -> str:
        mx = max(causas.values())
        top = [k for k, v in sorted(causas.items()) if v == mx]
        nomes = [f"“{esc(k)}”" for k in top]
        lista = nomes[0] if len(nomes) == 1 else ", ".join(nomes[:-1]) + " e " + nomes[-1]
        vezes = _plural(mx, "ocorrência", "ocorrências")
        if len(top) == 1:
            return f"A causa mais frequente de não cumprimento foi {lista}, com {vezes}"
        return f"As causas mais frequentes de não cumprimento foram {lista}, com {vezes} cada"
    insp = QE.get("inspecoes") or []
    ncs = QE.get("ncs") or []
    ncs_ab = [n for n in ncs if n.get("status") != "fechada"]
    ncs_venc = [n for n in ncs_ab if n.get("prazo") and n["prazo"][:10] < hoje.isoformat()]

    # ------------------------------------------------------------------- capa
    logo = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web", "icon-512.png")
    if os.path.exists(logo):
        H.append(Image(logo, width=3 * cm, height=3 * cm))
    H += [Spacer(1, 6), Pa("CUSTO PLANO CONSULTORIA LTDA", "centrob"), Spacer(1, 7 * cm),
          Pa(titulo, "centrob"), Pa(f"{esc(obra)}: {subtitulo}", "centro"),
          Spacer(1, 8 * cm), Pa("Guarulhos", "centro"), Pa(mes_ano, "centro"), PageBreak()]

    # --------------------------------------------------------- folha de rosto
    H += [Pa("CUSTO PLANO CONSULTORIA LTDA", "centrob"), Spacer(1, 7 * cm),
          Pa(titulo, "centrob"), Pa(f"{esc(obra)}: {subtitulo}", "centro"), Spacer(1, 1 * cm),
          Pa(f"Relatório gerencial da obra {esc(obra)}, com data de status em {ref:%d/%m/%Y}, referente a {mes_ano}. "
             f"Apresenta {assunto} a partir dos dados lançados no sistema Custo Plano. "
             "Elaborado conforme a ABNT NBR 10719.", "natureza"),
          Spacer(1, 7 * cm), Pa("Guarulhos", "centro"), Pa(mes_ano, "centro"), PageBreak()]

    # ------------------------------------------------------------------ resumo
    frases = [f"Este relatório apresenta {assunto} da obra {esc(obra)}, com data de status em {ref:%d/%m/%Y}."]
    if cons:
        frases[0] = (f"Este relatório apresenta o acompanhamento mensal da obra {esc(obra)}, com data de status em "
                     f"{ref:%d/%m/%Y}, nos três pilares de gestão: prazo, custo e qualidade.")
    if p and tipo in ("consolidado", "prazo", "medicao"):
        frases.append(f"O avanço físico real é de {_br(real)}%, contra {_br(prev)}% previstos na linha de base, "
                      f"um desvio de {_sn(desv)} pontos percentuais.")
    if ppc and tipo in ("consolidado", "curto"):
        frases.append(f"O PPC da semana de {_d(ppc['semana'])} foi de {ppc['ppc']}%.")
    if tipo == "curto" and causas:
        fc = frase_causas()
        frases.append(f"Nas últimas semanas, {fc[0].lower()}{fc[1:]}.")
    if rp and tipo == "plano":
        frases.append(f"Foram executados {rp['ok']} de {rp['tot']} pacotes de serviço por pavimento, e "
                      f"{_plural(rp['late'], 'pacote está atrasado', 'pacotes estão atrasados')}.")
    if tipo in ("consolidado", "custos"):
        if idc is not None:
            frases.append(f"{'No custo, o' if cons else 'O'} índice de desempenho de custo (IDC) é {_br(idc, 2)}"
                          + (f" e o índice de desempenho de prazo (IDP) é {_br(idp, 2)}." if idp is not None else "."))
        elif custos:
            frases.append("O orçamento está importado, mas ainda não há custo realizado lançado.")
    if tipo == "suprimentos" and sup:
        late = sum(1 for x in sup if x["grupo"] == "late")
        week = sum(1 for x in sup if x["grupo"] == "week")
        frases.append(f"Dos {len(sup)} pacotes de compra, {_plural(late, 'está atrasado', 'estão atrasados')} e "
                      f"{_plural(week, 'precisa ser comprado', 'precisam ser comprados')} nos próximos sete dias.")
    if tipo in ("consolidado", "qualidade", "inspecoes", "ncs") and indicadores is not None:
        ap_ = qual.get("aprovacao_1a_pct")
        frases.append(f"Nos 30 dias anteriores à data de status, foram registradas {_plural(qual.get('inspecoes_30d', 0), 'inspeção', 'inspeções')}"
                      + (f", com {ap_}% de aprovação na primeira inspeção," if ap_ is not None else "")
                      + f" e há {_plural(ind.get('ncs_abertas', 0), 'não conformidade aberta', 'não conformidades abertas')}.")
    if tipo == "modelos":
        mods = QE.get("modelos") or []
        frases.append(f"A obra tem {_plural(len(mods), 'modelo de FVS cadastrado', 'modelos de FVS cadastrados')}, "
                      "com os itens de verificação e os critérios de aceitação de cada serviço.")
    if tipo != "modelos":
        frases.append("A conclusão reúne os pontos de atenção e os próximos passos recomendados.")
    chaves = {"consolidado": "prazo; custo; qualidade; avanço físico; gestão de obras.",
              "prazo": "prazo; cronograma; avanço físico; curva S; linha de base.",
              "medicao": "medição; avanço físico; cronograma; prazo.",
              "plano": "plano de ataque; pavimentos; sequência executiva; prazo.",
              "curto": "planejamento de curto prazo; PPC; causas de não cumprimento.",
              "custos": "custo; orçamento; valor agregado; IDC; IDP.",
              "suprimentos": "suprimentos; compras; prazo de fornecedor.",
              "qualidade": "qualidade; inspeção; não conformidade.",
              "inspecoes": "qualidade; inspeção; ficha de verificação de serviço.",
              "ncs": "qualidade; não conformidade; ação corretiva.",
              "modelos": "qualidade; ficha de verificação de serviço; critério de aceitação."}[tipo]
    H += [Pa("RESUMO", "tsn"), Pa(" ".join(frases), "resumo"), Spacer(1, 14),
          Pa(f"<b>Palavras-chave:</b> {chaves}", "resumo"), PageBreak()]

    # ----------------------------------------------------------------- sumário
    toc = TableOfContents()
    toc.levelStyles = [est("toc1", fontName="Helvetica-Bold", leading=18, leftIndent=0, firstLineIndent=0),
                       est("toc2", leading=18, leftIndent=0, firstLineIndent=0)]
    toc.dotsMinLevel = 0
    H += [Pa("SUMÁRIO", "tsn"), toc, NextPageTemplate("texto"), PageBreak()]

    # -------------------------------------------------------------- introdução
    fontes = {"consolidado": [],
              "prazo": ["o cronograma importado e a medição de avanço físico", "o planejamento de curto prazo (PPC)"],
              "medicao": ["a medição de avanço físico de cada tarefa do cronograma"],
              "plano": ["o plano de ataque, com as datas de cada serviço por pavimento"],
              "curto": ["os apontamentos semanais dos pacotes de trabalho"],
              "custos": ["o orçamento e os custos lançados"],
              "suprimentos": ["as linhas de resumo do cronograma e os prazos de fornecedor informados na tela Suprimentos"],
              "qualidade": ["as fichas de verificação de serviço (FVS) e as não conformidades (NC)"],
              "inspecoes": ["as fichas de verificação de serviço (FVS) e as não conformidades (NC) registradas"],
              "ncs": ["o registro de não conformidades (NC)"],
              "modelos": ["os modelos de FVS cadastrados na obra"]}[tipo]
    if cons:
        if p:
            fontes.append("o cronograma importado e a medição de avanço físico")
        if plano:
            fontes.append("o plano de ataque")
        if custos:
            fontes.append("o orçamento e os custos lançados")
        if indicadores is not None:
            fontes.append("o planejamento de curto prazo e as fichas de verificação de serviço (FVS)")
    H.append(Pa("1 INTRODUÇÃO", "h1"))
    if cons:
        txt(f"Este relatório tem como objetivo apresentar a situação da obra {esc(obra)} em {ref:%d/%m/%Y}, "
            "data de status da medição, para apoiar as decisões da obra e do cliente. "
            "Prazo, custo e qualidade são analisados em conjunto, porque um desvio em um deles afeta os outros dois.")
    else:
        txt(f"Este relatório tem como objetivo apresentar {assunto} da obra {esc(obra)} na data de status de {ref:%d/%m/%Y}, "
            "para apoiar as decisões da obra e do cliente. O relatório consolidado, com prazo, custo e qualidade "
            "analisados em conjunto, é emitido no Painel da obra, no sistema Custo Plano.")
    txt(("Os dados vêm do sistema Custo Plano: " + "; ".join(fontes[:-1]) + ("; e " if len(fontes) > 2 else " e " if len(fontes) > 1 else "")
         + fontes[-1] + ". ")
        if fontes else "Os dados vêm do sistema Custo Plano. ")
    txt("Este documento tem caráter gerencial: organiza e analisa as informações de planejamento e controle, "
        "sem substituir o responsável técnico pela obra.")
    H.append(Pa("2 DESENVOLVIMENTO", "h1"))

    # ================================================================ seções
    def tab_prazo():
        linhas = [["Avanço físico real", f"{_br(real)}%", f"medição anterior: {_br(ant)}%"],
                  ["Avanço no período", f"{_sn(real - ant)} p.p.", "desde a medição anterior"],
                  ["Previsto na linha de base", f"{_br(prev)}%", f"em {data_status:%d/%m/%Y}"],
                  ["Desvio", Pa(f"<font color='{cor_desv(desv)}'><b>{_sn(desv)} p.p.</b></font>", "tr"),
                   "real menos previsto"]]
        if ppc and tipo != "medicao":
            linhas.append(["PPC (planejamento semanal)", Pa(f"<font color='{cor_desv(ppc['ppc'], 80)}'><b>{ppc['ppc']}%</b></font>", "tr"),
                           f"semana de {_d(ppc['semana'])}: {ppc['ok']} de {ppc['ok'] + ppc['nok']} pacotes cumpridos (meta: 80%)"])
        txt(f"A {prox()} resume o avanço físico da obra na data de status. O avanço real está "
            f"{'acima do previsto' if desv > 0.05 else 'abaixo do previsto' if desv < -0.05 else 'em linha com o previsto'} "
            "na linha de base.")
        add(tabela("Indicadores de prazo", ["Indicador", "Valor", "Observação"], linhas, [5.2 * cm, 2.8 * cm, W - 8 * cm],
                   direita=(1,)))

    def curva_s():
        prevista = curva_prevista(p, criterio, data_status)
        reais = sorted({(dt.datetime.fromisoformat(h["data_status"][:10]), float(h["avanco_real"])) for h in historico})
        reais = [r for r in reais if r[0].date() != data_status.date()] + [(data_status, real)]
        reais.sort()
        if not prevista:
            return
        dw, dh, L, B, Tp, R = W, 7 * cm, 30, 22, 16, 8
        d = Drawing(dw, dh)
        x0, x1 = prevista[0][0], prevista[-1][0]
        span = max((x1 - x0).total_seconds(), 1)
        X = lambda t: L + (t - x0).total_seconds() / span * (dw - L - R)  # noqa: E731
        Y = lambda v: B + v / 100 * (dh - B - Tp)  # noqa: E731
        for v in (0, 25, 50, 75, 100):
            d.add(Line(L, Y(v), dw - R, Y(v), strokeColor=CLARO, strokeWidth=0.6))
            d.add(String(L - 4, Y(v) - 3, f"{v}%", fontName="Helvetica", fontSize=8, fillColor=CINZA, textAnchor="end"))
        passo = max(1, len(prevista) // 10)
        for i, (t, _) in enumerate(prevista):
            if i % passo == 0:
                d.add(String(X(t), 8, f"{MESES[t.month - 1]}/{str(t.year)[2:]}", fontName="Helvetica", fontSize=8,
                             fillColor=CINZA, textAnchor="middle"))
        pts = []
        for t, v in prevista:
            pts += [X(t), Y(v)]
        d.add(PolyLine(pts, strokeColor=CINZA, strokeWidth=1.3, strokeDashArray=[4, 3]))
        reais = [r for r in reais if x0 <= r[0] <= x1] or reais[-1:]
        if reais and reais[0][0] > x0:
            reais = [(x0, 0.0)] + reais
        if len(reais) > 1:
            pts = []
            for t, v in reais:
                pts += [X(t), Y(v)]
            d.add(PolyLine(pts, strokeColor=NAVY, strokeWidth=2.2))
        for t, v in reais:
            d.add(Circle(X(t), Y(v), 2.4, fillColor=NAVY, strokeColor=colors.white, strokeWidth=0.6))
        xs = X(data_status)
        d.add(Line(xs, B, xs, Y(100), strokeColor=NAVY2, strokeWidth=0.8, strokeDashArray=[2, 2]))
        d.add(String(xs + 3, B + 4, f"data de status {data_status:%d/%m}", fontName="Helvetica-Bold", fontSize=8, fillColor=NAVY2))
        d.add(Rect(L, dh - 8, 14, 1.3, fillColor=CINZA, strokeColor=None))
        d.add(String(L + 18, dh - 10, "Previsto (linha de base)", fontName="Helvetica", fontSize=8, fillColor=CINZA))
        d.add(Rect(L + 130, dh - 8.5, 14, 2.2, fillColor=NAVY, strokeColor=None))
        d.add(String(L + 148, dh - 10, "Real (medições fechadas e atual)", fontName="Helvetica", fontSize=8, fillColor=CINZA))
        txt(f"A Figura {nfig[0] + 1} compara o avanço físico acumulado real com o previsto na linha de base, mês a mês.")
        H.append(KeepTogether([legenda("Figura", "Curva S do avanço físico acumulado"), d, fonte_dados()]))

    def tab_etapas():
        nmin = min(t.nivel for t in p.tarefas)
        etapas = [t for t in p.tarefas if t.nivel == nmin]
        if len(etapas) < 3:
            etapas = [t for t in p.tarefas if t.nivel <= nmin + 1]
        linhas = []
        for t in etapas[:28]:
            a = A[t.uid]
            if t.filhos:
                pr, pa_, pp = p.pct(a, "r"), p.pct(a, "b"), p.pct(a, "p")
            else:
                pr, pa_, pp = float(atual.get(t.uid, 0)), float(anterior.get(t.uid, 0)), p.previsto(t, data_status)
            dv = round(pr, 1) - round(pp, 1)
            linhas.append([t.eap, Pa(("<b>%s</b>" if t.nivel == nmin else "%s") % esc(t.nome)),
                           f"{_br(a['w'] / tot['w'] * 100 if tot['w'] else 0)}%", f"{_br(pa_)}%", f"{_br(pr)}%",
                           f"{_br(pp)}%", Pa(f"<font color='{cor_desv(dv)}'>{_sn(dv)}</font>", "tr")])
        txt(f"A {prox()} detalha o avanço por etapa: peso no avanço total, avanço na medição anterior, "
            "avanço atual, previsto e desvio em pontos percentuais.")
        add(tabela("Avanço físico por etapa", ["EAP", "Etapa", "Peso", "Anterior", "Atual", "Previsto", "Desvio (p.p.)"], linhas,
                   [1.4 * cm, W - 11.4 * cm, 1.7 * cm, 1.8 * cm, 1.7 * cm, 2 * cm, 2.8 * cm], direita=(2, 3, 4, 5, 6)))

    def tab_atrasos(limite):
        folhas = []
        for t in p.tarefas:
            if t.filhos or t.marco:
                continue
            pr, pp = float(atual.get(t.uid, 0)), p.previsto(t, data_status)
            if pp - pr > 0.5:
                folhas.append((A[t.uid]["w"] * (pp - pr), t, pr, pp))
        folhas.sort(key=lambda x: -x[0])
        if not folhas:
            txt("Não há tarefas com avanço abaixo do previsto na data de status.")
            return
        linhas = [[t.eap, Pa(esc(nome_t(t))), _d(t.lb_termino or t.termino), f"{_br(pp)}%", f"{_br(pr)}%",
                   Pa(f"<font color='#8E1616'>-{_br(pp - pr)}</font>", "tr")] for _, t, pr, pp in folhas[:limite]]
        txt(f"A {prox()} lista as tarefas com maior impacto no atraso, ordenadas pelo peso no avanço "
            "multiplicado pela diferença entre o previsto e o real"
            + (f". São exibidas as {limite} primeiras de {len(folhas)}." if len(folhas) > limite else "."))
        add(tabela("Tarefas com maior impacto no atraso", ["EAP", "Tarefa", "Término (LB)", "Previsto", "Atual", "Desvio (p.p.)"],
                   linhas, [1.6 * cm, W - 10.4 * cm, 2.4 * cm, 2 * cm, 1.8 * cm, 2.6 * cm], direita=(3, 4, 5)))

    def tab_marcos_cron():
        marcos = [t for t in p.tarefas if t.marco and (t.termino or t.lb_termino)]
        if not marcos:
            txt("O cronograma não tem marcos cadastrados.")
            return
        linhas = []
        for t in marcos[:30]:
            var = (t.termino - t.lb_termino).days if (t.termino and t.lb_termino) else None
            linhas.append([Pa(esc(t.nome)), _d(t.lb_termino), _d(t.termino),
                           destaque(_dias(var), var is not None and var > 0) if var is not None else "-"])
        txt(f"A {prox()} compara a data de cada marco na linha de base com a data atual do cronograma. "
            "Variação positiva indica atraso.")
        add(tabela("Marcos do cronograma", ["Marco", "Linha de base", "Atual", "Variação"], linhas,
                   [W - 8.4 * cm, 2.8 * cm, 2.8 * cm, 2.8 * cm], direita=(3,)))

    def tab_medicao():
        folhas = [t for t in p.tarefas if not t.filhos and not t.marco]
        linhas = []
        vals = []
        for t in folhas:
            pr, pa_, pp = float(atual.get(t.uid, 0)), float(anterior.get(t.uid, 0)), p.previsto(t, data_status)
            if max(pr, pa_, pp) < 0.05:
                continue
            vals.append((t, pr, pa_, pp))
        sem = len(folhas) - len(vals)
        for t, pr, pa_, pp in vals[:400]:
            dv = round(pr, 1) - round(pp, 1)
            linhas.append([t.eap, Pa(esc(nome_t(t))), f"{_br(pa_)}%", f"{_br(pr)}%", f"{_br(pp)}%",
                           Pa(f"<font color='{cor_desv(dv, -0.5)}'>{_sn(dv)}</font>", "tr")])
        txt(f"A {prox()} apresenta a medição de cada tarefa: avanço na medição anterior, avanço atual, previsto na "
            "linha de base e desvio em pontos percentuais"
            + (f". São exibidas as 400 primeiras de {len(vals)} tarefas" if len(vals) > 400 else "")
            + (f". {_plural(sem, 'tarefa ainda sem avanço medido nem previsto foi omitida', 'tarefas ainda sem avanço medido nem previsto foram omitidas')}"
               if sem else "") + ".")
        add(tabela("Medição por tarefa", ["EAP", "Tarefa", "Anterior", "Atual", "Previsto", "Desvio (p.p.)"], linhas,
                   [1.6 * cm, W - 10.4 * cm, 2 * cm, 2 * cm, 2 * cm, 2.8 * cm], direita=(2, 3, 4, 5)))

    def sec_plano(completo):
        tm = plano.get("termino") or {}
        txt("O plano de ataque da obra acompanha os pacotes de serviço por pavimento. "
            f"A {prox()} resume a execução e a aderência até a data base do plano.")
        linhas = [["Pacotes executados", f"{round(rp['ok'] / rp['tot'] * 100) if rp['tot'] else 0}%", f"{rp['ok']} de {rp['tot']} (serviço × pavimento)"],
                  ["Aderência até a data base", f"{round(rp['prev_ok'] / rp['prev'] * 100) if rp['prev'] else 0}%",
                   f"{rp['prev_ok']} de {rp['prev']} até {_d(rp['db'])}"],
                  ["Pacotes atrasados", destaque(str(rp["late"]), rp["late"]), "data prevista vencida, sem execução confirmada"]]
        if tm:
            linhas.append(["Término da obra", _d(tm.get("atual") or tm.get("lb")), f"linha de base {_d(tm.get('lb'))} ({_dias(tm.get('var'))})"])
        add(tabela("Plano de ataque", ["Indicador", "Valor", "Observação"], linhas, [5.2 * cm, 2.8 * cm, W - 8 * cm], direita=(1,)))
        if completo:
            secao("Marcos do plano de ataque")
        if plano.get("marcos"):
            if completo:
                txt(f"A {prox()} mostra a evolução dos marcos: data na linha de base, no mês anterior e atual.")
            add(tabela("Marcos da obra", ["Marco", "Linha de base", "Mês anterior", "Atual", "Var. LB", "Var. mês"],
                       [[m["nome"], _d(m["lb"]), _d(m["anterior"]), _d(m["atual"]), _dias(m["var_lb"]), _dias(m["var_mes"])]
                        for m in plano["marcos"]],
                       [W - 10.6 * cm, 2.2 * cm, 2.2 * cm, 2 * cm, 2.1 * cm, 2.1 * cm], direita=(4, 5)))
        elif completo:
            txt("O plano de ataque importado não tem marcos.")
        if not completo:
            return
        secao("Pacotes atrasados")
        at = []
        servs = (plano.get("atual") or {}).get("servicos") or []
        db = rp["db"]
        for pav in (plano.get("atual") or {}).get("pavimentos") or []:
            for j, s_ in enumerate(pav["datas"]):
                if not s_ or not db:
                    continue
                d_ = dt.date.fromisoformat(s_)
                if d_ <= db and not (pav["ok"] and pav["ok"][j]):
                    at.append((servs[j]["nome"] if j < len(servs) else f"Serviço {j + 1}", pav["rotulo"], d_, (db - d_).days))
        if not at:
            txt("Não há pacotes atrasados até a data base do plano.")
            return
        at.sort(key=lambda x: -x[3])
        txt(f"A {prox()} lista os pacotes com data prevista vencida e sem execução confirmada, do maior para o "
            "menor atraso.")
        add(tabela("Pacotes atrasados do plano de ataque", ["Serviço", "Pavimento", "Data prevista", "Dias de atraso"],
                   [[a[0], a[1], _d(a[2]), destaque(str(a[3]), True)] for a in at[:60]],
                   [W - 10 * cm, 3.4 * cm, 3.2 * cm, 3.4 * cm], direita=(3,)))

    def sec_custo_totais():
        txt("O custo é acompanhado pela análise de valor agregado. O valor planejado (VP) é o custo orçado do "
            "trabalho previsto até a data de status; o valor agregado (VA) é o custo orçado do trabalho executado; "
            "o custo realizado (CR) é o que foi efetivamente gasto. O índice de desempenho de custo (IDC) é a razão "
            "entre o VA e o CR: abaixo de 1,00, a obra gasta mais do que o orçado para o que já executou. O índice de "
            "desempenho de prazo (IDP) é a razão entre o VA e o VP: abaixo de 1,00, a obra executou menos do que o "
            f"planejado. A {prox()} apresenta os totais.")
        bdi = "BDI não informado" if abs((T.get("venda") or 0) - (T.get("bac") or 0)) < 0.5 else f"com BDI: {brl(T['venda'])}"
        linhas = [["Orçamento (custo direto)", brl(T["bac"]), bdi],
                  ["Valor planejado (VP)", brl(T["vp"]), "previsto até a data de status"],
                  ["Valor agregado (VA)", brl(T["va"]), "orçado do trabalho executado"],
                  ["Custo realizado (CR)", brl(T["cr"]), f"{_br(T['cr'] / T['bac'] * 100 if T['bac'] else 0)}% do orçamento"],
                  ["IDC", Pa(f"<font color='{cor_desv(idc or 1, 0.98)}'><b>{'-' if idc is None else _br(idc, 2)}</b></font>", "tr"),
                   "sem custo lançado" if idc is None else "VA / CR"],
                  ["IDP", "-" if idp is None else _br(idp, 2), "VA / VP"],
                  ["Estimativa no término", brl(T.get("eac")), "" if T.get("vac") is None else f"variação no término: {brl(T['vac'])}"]]
        add(tabela("Indicadores de custo", ["Indicador", "Valor", "Observação"], linhas, [5.2 * cm, 3.4 * cm, W - 8.6 * cm],
                   direita=(1,)))

    def sec_custo_etapas():
        vc = lambda v: "0" if abs(v) < 500 else ("-" if v < 0 else "+") + mil(abs(v))  # noqa: E731
        linhas = []
        for e in custos["etapas"]:
            cv = e["va"] - e["cr"]
            linhas.append([e["codigo"], Pa(esc(e["nome"])), mil(e["orcado"]), mil(e["vp"]), mil(e["va"]), mil(e["cr"]),
                           Pa(f"<font color='{cor_desv(cv, -500)}'>{vc(cv)}</font>", "tr"),
                           "-" if e["idc"] is None else _br(e["idc"], 2)])
        cvt = T["va"] - T["cr"]
        linhas.append(["", Pa("<b>Total</b>"), mil(T["bac"]), mil(T["vp"]), mil(T["va"]), mil(T["cr"]),
                       Pa(f"<b>{vc(cvt)}</b>", "tr"), "-" if idc is None else _br(idc, 2)])
        txt(f"A {prox()} mostra o custo por etapa, em milhares de reais. A coluna VC (VA − CR) é a variação de custo: "
            "negativa quando o gasto supera o orçado do que foi executado.")
        add(tabela("Custo por etapa (R$ mil)", ["Cód.", "Etapa", "Orçado", "VP", "VA", "CR", "VC (VA − CR)", "IDC"], linhas,
                   [1.1 * cm, W - 12.4 * cm, 2 * cm, 1.8 * cm, 1.8 * cm, 1.8 * cm, 2.6 * cm, 1.3 * cm],
                   direita=(2, 3, 4, 5, 6, 7), total=True))

    def sec_custo_tipos():
        tipos = custos.get("tipos") or []
        if len(tipos) < 2:
            txt("Os insumos ainda não estão classificados por tipo no orçamento.")
            return
        soma = sum(x["valor"] for x in tipos) or 1
        txt(f"A {prox()} mostra a composição do custo direto por tipo de insumo.")
        add(tabela("Composição do custo direto", ["Tipo de insumo", "Valor", "Com BDI", "%"],
                   [[x["nome"], brl(x["valor"]), brl(x["com_bdi"]), f"{_br(x['valor'] / soma * 100)}%"] for x in tipos],
                   [W - 10.2 * cm, 3.8 * cm, 3.8 * cm, 2.6 * cm], direita=(1, 2, 3)))

    def sec_custo_serie():
        serie = custos.get("serie") or []
        if not (0 < len(serie) <= 36):
            return
        # um mês por linha: cabe qualquer duração e qualquer valor sem quebrar os números
        mes = lambda v: mil(v) if v else ""  # noqa: E731
        txt(f"A {prox()} apresenta o cronograma financeiro: desembolso previsto e realizado, mês a mês, em milhares de reais.")
        add(tabela("Cronograma financeiro (R$ mil)", ["Mês", "Previsto no mês", "Previsto acumulado", "Realizado no mês",
                                                     "Realizado acumulado"],
                   [[f"{MESES[int(s_['mes'][5:7]) - 1]}/{s_['mes'][:4]}", mes(s_["previsto"]), mes(s_["previsto_acum"]),
                     *(("", "") if s_["mes"][:7] > f"{data_status:%Y-%m}" else (mes(s_["realizado"]), mes(s_["realizado_acum"])))]
                    for s_ in serie],
                   [W - 13.2 * cm, 3.3 * cm, 3.3 * cm, 3.3 * cm, 3.3 * cm], direita=(1, 2, 3, 4)))

    def tab_qualidade():
        ap = qual.get("aprovacao_1a_pct")
        servs = qual.get("servicos_inspecionados") or []
        txt("A qualidade é acompanhada pelas fichas de verificação de serviço (FVS) e pelas não conformidades (NC) "
            f"registradas no sistema. A {prox()} considera os 30 dias anteriores à data de status.")
        linhas = [["Inspeções realizadas", str(qual.get("inspecoes_30d", 0)),
                   (", ".join(servs[:6]) + ("..." if len(servs) > 6 else "")) if servs else "nenhum serviço inspecionado"],
                  ["Reinspeções", str(qual.get("reinspecoes_30d", 0)), ""],
                  ["Aprovação na primeira inspeção", "-" if ap is None else f"{ap}%", ""],
                  ["NCs abertas (na data de status)", destaque(str(ind.get("ncs_abertas", 0)), ind.get("ncs_abertas")),
                   f"{ind.get('ncs_altas', 0)} de gravidade alta, {qual.get('ncs_sem_responsavel', 0)} sem responsável"],
                  ["NCs com prazo vencido", destaque(str(ind.get("ncs_vencidas", 0)), ind.get("ncs_vencidas")), ""],
                  ["NCs fechadas nos últimos 30 dias", str(qual.get("ncs_fechadas_30d", 0)),
                   "" if qual.get("dias_medio_fechamento") is None else f"tempo médio de fechamento: {_br(qual['dias_medio_fechamento'])} dias"]]
        add(tabela("Indicadores de qualidade", ["Indicador", "Valor", "Observação"], linhas,
                   [6.6 * cm, 1.6 * cm, W - 8.2 * cm], direita=(1,)))
        por = qual.get("ncs_abertas_por_servico") or {}
        if por:
            txt(f"A {prox()} mostra em quais serviços estão as não conformidades abertas.")
            add(tabela("Não conformidades abertas por serviço", ["Serviço", "NCs abertas"],
                       [[k, str(v)] for k, v in sorted(por.items(), key=lambda x: -x[1])], [W - 3.5 * cm, 3.5 * cm], direita=(1,)))

    def tab_inspecoes(limite):
        if not insp:
            txt("Não há inspeções registradas nesta obra.")
            return
        txt(f"A {prox()} lista as inspeções registradas, da mais recente para a mais antiga"
            + (f". São exibidas as {limite} mais recentes de {len(insp)}." if len(insp) > limite else "."))
        add(tabela("Inspeções registradas", ["Código", "Data", "Serviço", "Local", "Inspetor", "Resultado"],
                   [[i["codigo"], _d(i["data"]), i["servico"], i["local"], i.get("inspetor") or "-",
                     destaque(("Aprovada" if i["resultado"] == "aprovada" else "Reprovada") + (" na reinspeção" if i.get("reinspecao_de") else ""),
                              i["resultado"] != "aprovada")] for i in insp[:limite]],
                   [1.8 * cm, 2.2 * cm, W - 12.2 * cm, 2.6 * cm, 2.2 * cm, 3.4 * cm], direita=(5,)))

    def tab_itens_nc():
        desde = (hoje - dt.timedelta(days=90)).isoformat()
        cont: dict[tuple, int] = {}
        for i in insp:
            if i["data"] < desde:
                continue
            for it in i.get("itens") or []:
                if it.get("status") == "nc":
                    k = (i["servico"], it.get("item") or "")
                    cont[k] = cont.get(k, 0) + 1
        if not cont:
            txt("Nos últimos 90 dias, nenhum item de verificação foi marcado como não conforme.")
            return
        txt(f"A {prox()} mostra os itens de verificação mais reprovados nos últimos 90 dias, que indicam onde "
            "reforçar o treinamento da equipe e o controle do serviço.")
        add(tabela("Itens não conformes mais frequentes (últimos 90 dias)", ["Serviço", "Item de verificação", "Ocorrências"],
                   [[k[0], k[1], str(v)] for k, v in sorted(cont.items(), key=lambda x: -x[1])[:15]],
                   [4.5 * cm, W - 7.5 * cm, 3 * cm], direita=(2,)))

    def tab_ncs(lista, titulo_, vazio):
        if not lista:
            txt(vazio)
            return
        linhas = []
        for n in lista[:80]:
            venc = n.get("status") != "fechada" and n.get("prazo") and n["prazo"][:10] < hoje.isoformat()
            serv = f"<br/><font color='#555555'>{esc(n['servico'])}</font>" if n.get("servico") else ""
            linhas.append([f"NC-{n['numero']:03d}", Pa(esc(n.get("descricao") or "") + serv),
                           GRAVIDADE.get(n.get("gravidade"), n.get("gravidade") or "-"), n.get("responsavel") or "Não definido",
                           destaque(_d(n.get("prazo")), venc) if n.get("prazo") else "-",
                           SITUACAO_NC.get(n.get("status"), n.get("status") or "-")])
        add(tabela(titulo_, ["NC", "Descrição e serviço", "Gravidade", "Responsável", "Prazo", "Situação"], linhas,
                   [1.6 * cm, W - 11.6 * cm, 2.2 * cm, 2.8 * cm, 2.2 * cm, 2.8 * cm], direita=(4,)))

    # ----------------------------------------------------------- por assunto
    if cons:
        secao("Prazo")
        if p:
            tab_prazo()
            curva_s()
            tab_etapas()
            tab_atrasos(12)
        elif not plano:
            txt("O cronograma da obra ainda não foi importado no sistema, por isso o avanço físico não é apresentado.")
        if plano:
            sec_plano(False)
        secao("Custo")
        if custos:
            sec_custo_totais()
            sec_custo_etapas()
            sec_custo_serie()
        else:
            txt("O orçamento da obra ainda não foi importado no sistema, por isso o custo não é apresentado.")
        secao("Qualidade")
        if indicadores is not None:
            tab_qualidade()
        else:
            txt("Os indicadores de qualidade não estão disponíveis para esta obra.")
    elif tipo in ("prazo", "medicao"):
        if not p:
            secao("Cronograma")
            txt("O cronograma da obra ainda não foi importado no sistema, por isso o avanço físico não é apresentado.")
        elif tipo == "prazo":
            secao("Avanço físico")
            tab_prazo()
            curva_s()
            tab_etapas()
            secao("Tarefas atrasadas")
            tab_atrasos(30)
            secao("Marcos")
            tab_marcos_cron()
        else:
            secao("Resumo da medição")
            tab_prazo()
            secao("Medição por tarefa")
            tab_medicao()
    elif tipo == "plano":
        secao("Execução do plano de ataque")
        if plano:
            sec_plano(True)
        else:
            txt("O plano de ataque ainda não foi importado no sistema.")
    elif tipo == "curto":
        secao("PPC semanal")
        if ppc_sem:
            txt("O percentual de pacotes concluídos (PPC) é a razão entre os pacotes cumpridos e os planejados para a "
                f"semana. A meta é de 80%. A {prox()} mostra as últimas semanas com apontamento.")
            add(tabela("PPC por semana", ["Semana de", "Pacotes apontados", "Cumpridos", "Não cumpridos", "PPC"],
                       [[_d(s_), str(ok + nok), str(ok), str(nok), destaque(f"{v}%", v < 80)] for s_, ok, nok, v in reversed(ppc_sem)],
                       [W - 12 * cm, 3.4 * cm, 2.8 * cm, 3.2 * cm, 2.6 * cm], direita=(1, 2, 3, 4)))
        else:
            txt("Ainda não há pacotes apontados no planejamento de curto prazo.")
        secao("Causas de não cumprimento")
        if causas:
            soma = sum(causas.values())
            txt(f"A {prox()} reúne as causas dos pacotes não cumpridos nas últimas semanas. Tratar as causas "
                "recorrentes é o principal meio de elevar o PPC.")
            add(tabela("Causas de não cumprimento", ["Causa", "Ocorrências", "%"],
                       [[k, str(v), f"{_br(v / soma * 100)}%"] for k, v in sorted(causas.items(), key=lambda x: -x[1])],
                       [W - 6 * cm, 3.2 * cm, 2.8 * cm], direita=(1, 2)))
        else:
            txt("Não há pacotes não cumpridos com causa registrada nas últimas semanas.")
        if semanas:
            ult = semanas[-1]
            nomes = {t.uid: t for t in p.tarefas} if p else {}
            linhas = []
            for uid_, a in curto[ult].items():
                t = nomes.get(uid_)
                linhas.append([t.eap if t else "-", Pa(esc(nome_t(t) if t else f"Tarefa {uid_}")),
                               destaque("Cumprido" if a.get("st") == "ok" else "Não cumprido", a.get("st") != "ok"),
                               a.get("causa") or "-"])
            secao("Pacotes da última semana")
            txt(f"A {prox()} mostra o apontamento de cada pacote na semana de {_d(ult)}.")
            add(tabela(f"Pacotes da semana de {_d(ult)}", ["EAP", "Pacote", "Situação", "Causa"], linhas,
                       [1.8 * cm, W - 9.4 * cm, 3.4 * cm, 4.2 * cm]))
    elif tipo == "custos":
        if custos:
            secao("Indicadores de custo")
            sec_custo_totais()
            secao("Custo por etapa")
            sec_custo_etapas()
            secao("Composição do custo direto")
            sec_custo_tipos()
            if custos.get("serie"):
                secao("Cronograma financeiro")
                sec_custo_serie()
        else:
            secao("Orçamento")
            txt("O orçamento da obra ainda não foi importado no sistema, por isso o custo não é apresentado.")
    elif tipo == "suprimentos":
        secao("Situação das compras")
        if not p:
            txt("Suprimentos usa as linhas de resumo do cronograma, que ainda não foi importado no sistema.")
        elif not sup:
            txt("O cronograma não tem linhas de resumo para formar os pacotes de compra.")
        else:
            grupos = [("Atrasados", "late"), ("Comprar nos próximos 7 dias", "week"), ("No prazo, pedidos ou entregues", "ok")]
            txt("Cada linha de resumo do cronograma é um pacote de compra. A data-limite de compra é a data de início do "
                f"pacote na obra menos o prazo do fornecedor. A {prox()} resume a situação.")
            add(tabela("Situação dos pacotes de compra", ["Situação", "Pacotes"],
                       [[g, destaque(str(sum(1 for x in sup if x["grupo"] == k)), k == "late" and any(x["grupo"] == k for x in sup))]
                        for g, k in grupos], [W - 3.5 * cm, 3.5 * cm], direita=(1,)))
            urg = sorted([x for x in sup if x["grupo"] in ("late", "week")], key=lambda x: x["buy"])
            secao("Compras atrasadas e da semana")
            if urg:
                txt(f"A {prox()} lista as compras que precisam de ação imediata, em ordem de data-limite de compra.")
                add(tabela("Compras atrasadas e da semana", ["Pacote", "Necessidade", "Comprar até", "Fornecedor", "Situação"],
                           [[Pa(esc(x["nome"])), _d(x["need"]), _d(x["buy"]), x["forn"] or "-", destaque(x["sit"], x["grupo"] == "late")]
                            for x in urg[:60]],
                           [W - 12.2 * cm, 2.6 * cm, 2.4 * cm, 3.4 * cm, 3.8 * cm]))
            else:
                txt("Não há compras atrasadas nem com data-limite nos próximos sete dias.")
            secao("Todos os pacotes de compra")
            txt(f"A {prox()} traz todos os pacotes, com o prazo do fornecedor e o status do pedido.")
            add(tabela("Pacotes de compra", ["Pacote", "Necessidade", "Prazo", "Comprar até", "Status do pedido", "Situação"],
                       [[Pa(esc(x["nome"])), _d(x["need"]), _plural(x["lead"], "dia", "dias"), _d(x["buy"]),
                         ETAPA_SUP.get(x["st"], x["st"]), destaque(x["sit"], x["grupo"] == "late")] for x in sup[:200]],
                       [W - 11.8 * cm, 2.6 * cm, 1.7 * cm, 2.4 * cm, 2.4 * cm, 2.7 * cm]))
    elif tipo == "qualidade":
        secao("Indicadores de qualidade")
        tab_qualidade()
        secao("Inspeções recentes")
        tab_inspecoes(15)
        secao("Não conformidades abertas")
        if ncs_ab:
            txt(f"A {prox()} lista as não conformidades ainda não fechadas.")
        tab_ncs(sorted(ncs_ab, key=lambda n: (n.get("prazo") or "9999")), "Não conformidades abertas",
                "Não há não conformidades abertas.")
    elif tipo == "inspecoes":
        secao("Indicadores de qualidade")
        tab_qualidade()
        secao("Inspeções registradas")
        tab_inspecoes(80)
        secao("Itens não conformes mais frequentes")
        tab_itens_nc()
    elif tipo == "ncs":
        secao("Situação das não conformidades")
        fech = [n for n in ncs if n.get("status") == "fechada"]
        txt(f"A {prox()} resume as não conformidades registradas na obra.")
        add(tabela("Situação das não conformidades", ["Indicador", "Quantidade"],
                   [["Registradas", str(len(ncs))], ["Abertas ou em tratamento", destaque(str(len(ncs_ab)), ncs_ab)],
                    ["Com prazo vencido", destaque(str(len(ncs_venc)), ncs_venc)],
                    ["Abertas de gravidade alta", destaque(str(sum(1 for n in ncs_ab if n.get("gravidade") == "alta")),
                                                             any(n.get("gravidade") == "alta" for n in ncs_ab))],
                    ["Abertas sem responsável", destaque(str(sum(1 for n in ncs_ab if not n.get("responsavel"))),
                                                 any(not n.get("responsavel") for n in ncs_ab))],
                    ["Fechadas", str(len(fech))]],
                   [W - 3.5 * cm, 3.5 * cm], direita=(1,)))
        secao("Não conformidades abertas")
        if ncs_ab:
            txt(f"A {prox()} lista as não conformidades abertas ou em tratamento, com responsável e prazo. "
                "Prazos vencidos aparecem em destaque.")
        tab_ncs(sorted(ncs_ab, key=lambda n: (n.get("prazo") or "9999")), "Não conformidades abertas",
                "Não há não conformidades abertas.")
        secao("Não conformidades fechadas")
        if fech:
            txt(f"A {prox()} lista as não conformidades já fechadas, da mais recente para a mais antiga.")
        tab_ncs(sorted(fech, key=lambda n: n.get("fechada_em") or "", reverse=True), "Não conformidades fechadas",
                "Ainda não há não conformidades fechadas.")
    elif tipo == "modelos":
        mods = QE.get("modelos") or []
        secao("Modelos cadastrados")
        if not mods:
            txt("Não há modelos de FVS cadastrados nesta obra.")
        else:
            txt(f"A {prox()} lista os modelos de ficha de verificação de serviço usados nas inspeções da obra.")
            add(tabela("Modelos de FVS", ["Código", "Serviço", "Itens"],
                       [[m["codigo"], m["servico"], str(len(m.get("itens") or []))] for m in mods],
                       [2.4 * cm, W - 4.8 * cm, 2.4 * cm], direita=(2,)))
            secao("Itens e critérios de cada modelo")
            txt("As tabelas a seguir trazem, para cada modelo, os itens verificados e o critério de aceitação de cada um.")
            for m in mods:
                add(tabela(f"Itens e critérios de aceitação da {m['codigo']} ({m['servico']})", ["Nº", "Item de verificação", "Critério de aceitação"],
                           [[str(k), Pa(esc(it.get("item") or "")), Pa(esc(it.get("criterio") or ""))]
                            for k, it in enumerate(m.get("itens") or [], start=1)],
                           [1 * cm, (W - 1 * cm) * 0.42, (W - 1 * cm) * 0.58]))

    # --------------------------------------------------------------- conclusão
    # cada alínea: diagnóstico e ação numa frase só, separados por ponto e vírgula
    H.append(Pa("3 CONCLUSÃO", "h1"))
    itens = []
    tm = (plano or {}).get("termino") or {}
    var_fim = tm.get("var")
    if p and tipo in ("consolidado", "prazo", "medicao"):
        n_atr = sum(1 for t in p.tarefas if not t.filhos and not t.marco
                    and p.previsto(t, data_status) - float(atual.get(t.uid, 0)) > 0.5)
        if desv < -5:
            itens.append(f"<b>Prazo:</b> a obra está {_br(-desv)} p.p. atrás do previsto, acima da tolerância de 5 p.p.; "
                         "rever o plano de ataque das tarefas de maior impacto no atraso e confirmar com a obra os recursos "
                         "para recuperar o ritmo")
        elif desv < -0.05:
            itens.append(f"<b>Prazo:</b> desvio de {_br(desv)} p.p., dentro da tolerância de 5 p.p."
                         + (f", mas com {_plural(n_atr, 'tarefa abaixo do previsto', 'tarefas abaixo do previsto')}" if n_atr else "")
                         + "; manter o acompanhamento semanal" + (" e recuperar essas frentes" if n_atr else ""))
        else:
            itens.append(f"<b>Prazo:</b> o avanço real está em linha com o previsto ou à frente "
                         f"({_sn(desv)} p.p.)")
    if tipo in ("consolidado", "prazo", "plano") and var_fim and var_fim > 0:
        itens.append(f"<b>Término:</b> entrega projetada para {_d(tm.get('atual'))}, {_plural(var_fim, 'dia', 'dias')} depois da "
                     f"linha de base ({_d(tm.get('lb'))}); definir as ações de recuperação do prazo")
    elif tipo == "prazo" and p:
        atrasados = [t for t in p.tarefas if t.marco and t.termino and t.lb_termino and (t.termino - t.lb_termino).days > 0]
        if atrasados:
            itens.append(f"<b>Marcos:</b> {_plural(len(atrasados), 'marco está', 'marcos estão')} com data posterior à da "
                         "linha de base; definir as ações de recuperação do prazo")
    if rp and tipo in ("consolidado", "plano"):
        if rp["late"]:
            itens.append(f"<b>Plano de ataque:</b> {_plural(rp['late'], 'pacote atrasado', 'pacotes atrasados')}; reprogramar "
                         "esses pacotes na reunião semanal de planejamento e confirmar as frentes liberadas")
        elif tipo == "plano":
            itens.append("<b>Plano de ataque:</b> todos os pacotes previstos até a data base do plano foram executados")
    if tipo in ("consolidado", "curto"):
        if ppc and ppc["ppc"] < 80:
            itens.append(f"<b>Planejamento semanal:</b> PPC de {ppc['ppc']}%, abaixo da meta de 80%; analisar as causas "
                         "registradas e definir ações para as que se repetem")
        elif ppc:
            itens.append(f"<b>Planejamento semanal:</b> PPC de {ppc['ppc']}%, dentro da meta de 80%")
        if tipo == "curto" and causas:
            mx = max(causas.values())
            top = [k for k, v in causas.items() if v == mx]
            itens.append(f"<b>Causas:</b> {frase_causas()[0].lower()}{frase_causas()[1:]}; tratar "
                         + ("essa restrição" if len(top) == 1 else "essas restrições") + " antes de liberar os pacotes da próxima semana")
    if tipo in ("consolidado", "custos"):
        if idc is not None:
            if idc < 0.98:
                itens.append(f"<b>Custo:</b> IDC de {_br(idc, 2)}, ou seja, a obra gasta mais do que o orçado para o que executou; "
                             "conferir os lançamentos de custo realizado e as etapas com maior variação de custo")
            else:
                itens.append(f"<b>Custo:</b> IDC de {_br(idc, 2)}, dentro do orçado para o que foi executado")
            if T.get("vac") is not None and T["vac"] < -0.5 and tipo == "custos":
                itens.append(f"<b>Estimativa no término:</b> {brl(T.get('eac'))}, {brl(-T['vac'])} acima do orçamento; "
                             "revisar as etapas com maior variação e negociar as contratações ainda pendentes")
            if idp is not None and idp < 0.98 and tipo == "custos":
                itens.append(f"<b>Valor agregado:</b> IDP de {_br(idp, 2)}, ou seja, a obra executou menos do que o planejado "
                             "para a data de status; recuperar o ritmo das frentes atrasadas")
        elif custos:
            itens.append("<b>Custo:</b> ainda não há custo realizado; lançá-lo na tela Custos para que o IDC seja calculado")
    if tipo == "suprimentos" and sup:
        late = [x for x in sup if x["grupo"] == "late"]
        week = [x for x in sup if x["grupo"] == "week"]
        if late:
            itens.append(f"<b>Compras atrasadas:</b> {_plural(len(late), 'pacote passou', 'pacotes passaram')} da data-limite de "
                         "compra; emitir os pedidos imediatamente e negociar o prazo de entrega com o fornecedor ou ajustar "
                         "a sequência da obra")
        if week:
            nomes_ = ", ".join(f"{esc(x['nome'])} (até {_d(x['buy'])})" for x in week[:3])
            itens.append(f"<b>Compras da semana:</b> {_plural(len(week), 'pacote precisa ser comprado', 'pacotes precisam ser comprados')} "
                         f"nos próximos sete dias ({nomes_}" + (" e outros" if len(week) > 3 else "") + "); emitir "
                         + ("o pedido" if len(week) == 1 else "os pedidos") + " até a data-limite")
        if not late and not week:
            itens.append("<b>Suprimentos:</b> não há compras atrasadas nem urgentes")
        sem_forn = sum(1 for x in sup if not x["forn"] and x["grupo"] != "ok")
        if sem_forn:
            itens.append(f"<b>Fornecedores:</b> {_plural(sem_forn, 'pacote pendente está', 'pacotes pendentes estão')} sem "
                         "fornecedor informado; definir o fornecedor antes da data-limite")
    if tipo in ("consolidado", "qualidade", "inspecoes") and indicadores is not None:
        n_insp = qual.get("inspecoes_30d", 0)
        if not n_insp:
            itens.append("<b>Inspeções:</b> não houve inspeção registrada nos 30 dias anteriores à data de status; programar as "
                         "FVS dos serviços em execução")
        else:
            ap_ = qual.get("aprovacao_1a_pct")
            itens.append(f"<b>Inspeções:</b> {_plural(n_insp, 'inspeção registrada', 'inspeções registradas')} nos 30 dias anteriores "
                         "à data de status" + (f", com {ap_}% de aprovação na primeira inspeção" if ap_ is not None else "")
                         + ("; reforçar o controle dos serviços com mais reprovações" if ap_ is not None and ap_ < 90 else ""))
    if tipo in ("consolidado", "qualidade", "inspecoes", "ncs"):
        venc = len(ncs_venc) if QE else ind.get("ncs_vencidas", 0)
        altas = (sum(1 for n in ncs_ab if n.get("gravidade") == "alta") if QE else ind.get("ncs_altas", 0))
        sem_resp = (sum(1 for n in ncs_ab if not n.get("responsavel")) if QE else qual.get("ncs_sem_responsavel", 0))
        abertas = len(ncs_ab) if QE else ind.get("ncs_abertas", 0)
        if venc:
            itens.append(f"<b>Não conformidades vencidas:</b> {_plural(venc, 'não conformidade está', 'não conformidades estão')} "
                         "com prazo vencido; cobrar " + ("o responsável" if venc == 1 else "os responsáveis")
                         + ", registrar a ação corretiva e verificar a eficácia na reinspeção")
        if altas:
            itens.append(f"<b>Gravidade alta:</b> {_plural(altas, 'não conformidade aberta é', 'não conformidades abertas são')} "
                         "de gravidade alta; priorizar o tratamento")
        if sem_resp:
            itens.append(f"<b>Responsáveis:</b> {_plural(sem_resp, 'não conformidade aberta está', 'não conformidades abertas estão')} "
                         "sem responsável; definir " + ("o responsável pelo tratamento" if sem_resp == 1 else "o responsável por cada uma"))
        if abertas and not venc and tipo != "inspecoes":
            itens.append(f"<b>Não conformidades:</b> {_plural(abertas, 'não conformidade aberta', 'não conformidades abertas')} dentro do prazo; acompanhar o fechamento")
        if tipo == "ncs" and not abertas:
            itens.append("<b>Não conformidades:</b> não há pendências abertas")
    if tipo == "modelos":
        mods = QE.get("modelos") or []
        itens.append(f"<b>Modelos de FVS:</b> {_plural(len(mods), 'modelo cadastrado', 'modelos cadastrados')}; revisar os critérios "
                     "de aceitação sempre que o projeto ou a especificação do serviço mudar")
    if not itens:
        itens.append("não há dados suficientes para conclusões nesta data de status")
    abre = Pa("Os principais pontos de atenção na data de status são:" if len(itens) > 1
              else "O principal ponto de atenção na data de status é:", "txt")
    alineas = [Pa(f"{'abcdefghijklmn'[i]})&nbsp;&nbsp;{t.rstrip('.')}{';' if i < len(itens) - 1 else '.'}", "item")
               for i, t in enumerate(itens)]
    H.append(KeepTogether([abre, alineas[0]]))  # a frase de abertura não fica sozinha no fim da página
    H += alineas[1:]
    obs = Table([[""]], colWidths=[W], rowHeights=[3 * cm])
    obs.setStyle(TableStyle([("BOX", (0, 0), (-1, -1), 0.6, LINHA)]))
    ass = Table([["", "", ""], [Pa("Custo Plano Consultoria", "small"),
                                Pa("Engenheiro da obra", "small"), Pa("Cliente / gerenciadora", "small")]],
                colWidths=[W / 3] * 3, rowHeights=[2 * cm, None])
    ass.setStyle(TableStyle([("LINEBELOW", (0, 0), (0, 0), 0.7, NAVY), ("LINEBELOW", (1, 0), (1, 0), 0.7, NAVY),
                             ("LINEBELOW", (2, 0), (2, 0), 0.7, NAVY), ("LEFTPADDING", (0, 0), (-1, -1), 6),
                             ("RIGHTPADDING", (0, 0), (-1, -1), 6)]))
    H.append(KeepTogether([Spacer(1, 10), Pa("Observações:", "t"), obs, Spacer(1, 6), ass]))

    # ------------------------------------------------------------- referências
    H += [PageBreak(), Pa("REFERÊNCIAS", "tsnref"),
          Pa("ASSOCIAÇÃO BRASILEIRA DE NORMAS TÉCNICAS. <b>NBR 10719</b>: informação e documentação: relatório técnico e/ou "
             "científico: apresentação. Rio de Janeiro: ABNT, 2015.", "ref"),
          Pa(f"CUSTO PLANO CONSULTORIA. <b>Sistema Custo Plano</b>: dados da obra {esc(obra)}. Guarulhos, {ref.year}. "
             f"Acesso em: {gerado.day} {MESES[gerado.month - 1]}. {gerado.year}.", "ref")]

    doc.multiBuild(H)
    return buf.getvalue()


def fvs_pdf(i: dict, pegar_foto, obra: str) -> bytes:
    """Ficha de Verificação de Serviço preenchida, com fotos e assinatura."""
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.lib.utils import ImageReader
    from reportlab.platypus import Image, KeepTogether, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    # cores padrão da Custo Plano; margens ABNT (superior e esquerda 3 cm, inferior e direita 2 cm)
    NAVY, LINE, GROUND, MUTED = colors.HexColor("#002455"), colors.HexColor("#B8C2CC"), colors.HexColor("#ECF0F1"), colors.HexColor("#555555")
    OK, NC = colors.HexColor("#002455"), colors.HexColor("#8E1616")
    st = lambda n, **k: ParagraphStyle(n, **{"fontName": "Helvetica", "fontSize": 9, "leading": 12, "textColor": NAVY, **k})  # noqa: E731
    h1, t, tb, sm = st("h1", fontName="Helvetica-Bold", fontSize=17, leading=21), st("t", fontSize=8.5, leading=11), \
        st("tb", fontName="Helvetica-Bold", fontSize=8.5, leading=11), st("sm", fontSize=7.8, leading=10, textColor=MUTED)
    esc = lambda s: (s or "").replace("&", "&amp;").replace("<", "&lt;")  # noqa: E731
    br = lambda s: f"{s[8:10]}/{s[5:7]}/{s[:4]}" if s else "-"  # noqa: E731

    def img(fid, w, h):
        f = pegar_foto(fid) if fid else None
        if not f:
            return None
        try:
            ir = ImageReader(io.BytesIO(f["dados"]))
            iw, ih = ir.getSize()
            k = min(w / iw, h / ih)
            return Image(io.BytesIO(f["dados"]), iw * k, ih * k)
        except Exception:
            return None

    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=30 * mm, rightMargin=20 * mm, topMargin=30 * mm, bottomMargin=20 * mm,
                            title=f"{i['codigo']} - {i['servico']} - {i['local']}", author="Custo Plano Consultoria")
    W = doc.width
    ap = i["resultado"] == "aprovada"
    H = [Paragraph(f"Ficha de Verificação de Serviço · {esc(i['codigo'])}", h1), Spacer(1, 4)]
    cab = Table([
        [Paragraph("<b>Obra</b>", t), Paragraph(esc(obra), t), Paragraph("<b>Serviço</b>", t), Paragraph(esc(i["servico"]), t)],
        [Paragraph("<b>Local</b>", t), Paragraph(esc(i["local"]), t), Paragraph("<b>Data</b>", t), Paragraph(br(i["data"]), t)],
        [Paragraph("<b>Inspetor</b>", t), Paragraph(esc(i["inspetor"]) or "-", t), Paragraph("<b>Resultado</b>", t),
         Paragraph(f"<font color='{'#002455' if ap else '#8E1616'}'><b>{'APROVADA' if ap else 'REPROVADA'}</b></font>"
                   + (" (reinspeção)" if i.get("reinspecao_de") else ""), t)],
    ], colWidths=[20 * mm, W / 2 - 20 * mm, 22 * mm, W / 2 - 22 * mm])
    cab.setStyle(TableStyle([("BOX", (0, 0), (-1, -1), 0.6, LINE), ("INNERGRID", (0, 0), (-1, -1), 0.4, LINE),
                             ("BACKGROUND", (0, 0), (0, -1), GROUND), ("BACKGROUND", (2, 0), (2, -1), GROUND),
                             ("VALIGN", (0, 0), (-1, -1), "MIDDLE")]))
    H += [cab, Spacer(1, 8)]
    lab = {"ok": ("C", OK, "Conforme"), "nc": ("NC", NC, "Não conforme"), "na": ("NA", MUTED, "Não se aplica")}
    linhas = [[Paragraph("<b>Item de verificação</b>", t), Paragraph("<b>Critério</b>", t), Paragraph("<b>Situação</b>", t), Paragraph("<b>Observação</b>", t)]]
    estilo = [("BACKGROUND", (0, 0), (-1, 0), GROUND), ("LINEBELOW", (0, 0), (-1, -1), 0.4, LINE), ("VALIGN", (0, 0), (-1, -1), "TOP"),
              ("BOX", (0, 0), (-1, -1), 0.6, LINE)]
    for n, it in enumerate(i["itens"], start=1):
        c, cor, nome = lab[it["status"]]
        linhas.append([Paragraph(f"{n}. {esc(it['item'])}", tb), Paragraph(esc(it["criterio"]), t),
                       Paragraph(f"<font color='#{cor.hexval()[2:]}'><b>{nome}</b></font>", t), Paragraph(esc(it.get("obs")), t)])
    tab = Table(linhas, colWidths=[48 * mm, W - 48 * mm - 26 * mm - 42 * mm, 26 * mm, 42 * mm], repeatRows=1)
    tab.setStyle(TableStyle(estilo))
    H += [tab]
    if i.get("obs"):
        H += [Spacer(1, 6), Paragraph("<b>Observações gerais:</b> " + esc(i["obs"]), t)]
    if i.get("ncs"):
        H += [Spacer(1, 6), Paragraph("<b>Não conformidades geradas:</b> " + "; ".join(
            f"NC-{x['numero']:03d} ({'fechada' if x['status'] == 'fechada' else 'aberta'})" for x in i["ncs"]), t)]
    fotos = [(n, fid) for n, it in enumerate(i["itens"], start=1) for fid in (it.get("fotos") or [])]
    if fotos:
        H += [Spacer(1, 8), Paragraph("<b>Registro fotográfico</b>", tb), Spacer(1, 4)]
        cel = []
        for n, fid in fotos[:12]:
            im = img(fid, W / 3 - 6 * mm, 55 * mm)
            if im:
                cel.append([im, Paragraph(f"Item {n}", sm)])
        grade = [cel[k:k + 3] for k in range(0, len(cel), 3)]
        for linha in grade:
            linha += [""] * (3 - len(linha))
            g = Table([linha], colWidths=[W / 3] * 3)
            g.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP")]))
            H.append(g)
    assin = img(i.get("assinatura"), 60 * mm, 22 * mm) if i.get("assinatura") else None
    bloco = Table([[assin or "", ""], [Paragraph("Inspetor: " + (esc(i["inspetor"]) or ""), sm), Paragraph("Engenheiro responsável", sm)]],
                  colWidths=[W / 2] * 2, rowHeights=[24 * mm, None])
    bloco.setStyle(TableStyle([("LINEBELOW", (0, 0), (0, 0), 0.7, NAVY), ("LINEBELOW", (1, 0), (1, 0), 0.7, NAVY),
                               ("VALIGN", (0, 0), (-1, 0), "BOTTOM"), ("LEFTPADDING", (0, 0), (-1, -1), 8), ("RIGHTPADDING", (0, 0), (-1, -1), 8)]))
    H += [Spacer(1, 12), KeepTogether([bloco])]
    H += [Spacer(1, 8), Paragraph(f"Custo Plano Consultoria · gerado em {dt.datetime.now():%d/%m/%Y %H:%M}", sm)]
    doc.build(H)
    return buf.getvalue()
