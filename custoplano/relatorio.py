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
    return x.strftime("%d/%m/%y")


def _dias(n) -> str:
    if n is None:
        return "-"
    return ("+" if n > 0 else "") + f"{n} d"


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


def gerar(p: Projeto | None, atual: dict, anterior: dict, data_status: dt.datetime, criterio: str | None,
          historico: list, plano: dict | None, obra: str, custos: dict | None = None,
          indicadores: dict | None = None) -> bytes:
    """Relatório no padrão ABNT (NBR 10719): capa, folha de rosto, resumo, sumário, introdução,
    desenvolvimento em prazo, custo e qualidade, conclusão e referências."""
    from reportlab.graphics.shapes import Circle, Drawing, Line, PolyLine, Rect, String
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY, TA_LEFT
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import cm
    from reportlab.platypus import (BaseDocTemplate, Frame, Image, KeepTogether, NextPageTemplate, PageBreak,
                                    PageTemplate, Paragraph, Spacer, Table, TableStyle)
    from reportlab.platypus.tableofcontents import TableOfContents

    # cores padrão da Custo Plano (projetos/_modelo/marca/cores-padrao.md)
    NAVY, NAVY2, CLARO = colors.HexColor("#002455"), colors.HexColor("#261FB3"), colors.HexColor("#ECF0F1")
    PRETO, CINZA, LINHA = colors.black, colors.HexColor("#555555"), colors.HexColor("#B8C2CC")
    ind = indicadores or {}
    qual = ind.get("qualidade") or {}

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
        "h1": est("h1", fontName="Helvetica-Bold", spaceBefore=18, spaceAfter=12),
        "h2": est("h2", fontName="Helvetica-Bold", spaceBefore=12, spaceAfter=12),
        "tsn": est("tsn", fontName="Helvetica-Bold", alignment=TA_CENTER, spaceAfter=18),
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
    esc = lambda s: str(s).replace("&", "&amp;").replace("<", "&lt;")  # noqa: E731
    Pa = lambda t, s="t": Paragraph(t, S[s])  # noqa: E731
    cor_desv = lambda v, lim=-0.05: "#8E1616" if v < lim else "#002455"  # noqa: E731
    ntab, nfig = [0], [0]

    def legenda(tipo, texto):
        cont = ntab if tipo == "Tabela" else nfig
        cont[0] += 1
        return Pa(f"{tipo} {cont[0]} – {texto}", "leg")

    def fonte_dados(texto="Sistema Custo Plano"):
        return Pa(f"Fonte: {texto} ({data_status.year}).", "fonte")

    def tabela(titulo, cab, linhas, larg, direita=(), total=False, fonte="Sistema Custo Plano"):
        """Tabela aberta nas laterais (padrão IBGE), cabeçalho azul-marinho, legenda acima e fonte abaixo."""
        dados = [[Pa(h, "thr" if i in direita else "th") for i, h in enumerate(cab)]]
        for ln in linhas:
            dados.append([c if not isinstance(c, str) else Pa(c, "tr" if i in direita else "t") for i, c in enumerate(ln)])
        t = Table(dados, colWidths=larg, repeatRows=1)
        estilo = [("BACKGROUND", (0, 0), (-1, 0), NAVY), ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                  ("LINEABOVE", (0, 0), (-1, 0), 0.8, NAVY), ("LINEBELOW", (0, -1), (-1, -1), 0.8, NAVY),
                  ("LINEBELOW", (0, 1), (-1, -2), 0.3, LINHA),
                  ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                  ("LEFTPADDING", (0, 0), (-1, -1), 4), ("RIGHTPADDING", (0, 0), (-1, -1), 4)]
        if total:
            estilo.append(("BACKGROUND", (0, -1), (-1, -1), CLARO))
        t.setStyle(TableStyle(estilo))
        return KeepTogether([legenda("Tabela", titulo), t, fonte_dados(fonte)]) if len(dados) <= 20 else \
            [legenda("Tabela", titulo), t, fonte_dados(fonte)]

    def add(lista, x):
        lista.extend(x) if isinstance(x, list) else lista.append(x)

    ref = data_status if p else dt.datetime.combine((plano and resumo_plano(plano)["db"]) or dt.date.today(), dt.time(17))
    mes_ano = f"{MESES_EXT[ref.month - 1]} de {ref.year}"
    gerado = dt.datetime.now()

    # ------------------------------------------------------------------ páginas
    class Doc(BaseDocTemplate):
        def afterFlowable(self, f):
            if isinstance(f, Paragraph) and f.style.name in ("h1", "h2"):
                nivel = 0 if f.style.name == "h1" else 1
                self.notify("TOCEntry", (nivel, f.getPlainText(), self.page - 1))

    def pg_texto(c, doc):
        w, h = A4
        c.saveState()
        c.setFont("Helvetica", 10)
        c.setFillColor(PRETO)
        c.drawRightString(w - 2 * cm, h - 2 * cm, str(doc.page - 1))  # a capa não conta; a folha de rosto é a 1
        c.setFillColor(CINZA)
        c.setFont("Helvetica", 8)
        c.drawString(3 * cm, 1.2 * cm, f"Gerado em {gerado:%d/%m/%Y %H:%M} pelo sistema Custo Plano.")
        c.restoreState()

    buf = io.BytesIO()
    # margens ABNT: superior e esquerda 3 cm, inferior e direita 2 cm
    doc = Doc(buf, pagesize=A4, leftMargin=3 * cm, rightMargin=2 * cm, topMargin=3 * cm, bottomMargin=2 * cm,
              title=f"Relatório mensal - {obra}", author="Custo Plano Consultoria")
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
        real, prev, ant = p.pct(tot, "r"), p.pct(tot, "p"), p.pct(tot, "b")
        desv = real - prev
    T = (custos or {}).get("totais") or {}
    idc, idp = T.get("idc"), T.get("idp")
    ppc = ind.get("ppc")
    brl = lambda v: "-" if v is None else ("-" if v < -0.5 else "") + "R$ " + f"{abs(v):,.0f}".replace(",", ".")  # noqa: E731
    # tabelas com várias colunas em R$ mil, para os valores caberem numa linha só
    mil = lambda v: "-" if v is None else f"{v / 1000:,.0f}".replace(",", ".")  # noqa: E731

    # ------------------------------------------------------------------- capa
    logo = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web", "icon-512.png")
    if os.path.exists(logo):
        H.append(Image(logo, width=3 * cm, height=3 * cm))
    H += [Spacer(1, 6), Pa("CUSTO PLANO CONSULTORIA LTDA", "centrob"), Spacer(1, 7 * cm),
          Pa("RELATÓRIO MENSAL DE AVANÇO", "centrob"), Pa(f"{esc(obra)}: prazo, custo e qualidade", "centro"),
          Spacer(1, 8 * cm), Pa("Guarulhos", "centro"), Pa(mes_ano, "centro"), PageBreak()]

    # --------------------------------------------------------- folha de rosto
    H += [Pa("CUSTO PLANO CONSULTORIA LTDA", "centrob"), Spacer(1, 7 * cm),
          Pa("RELATÓRIO MENSAL DE AVANÇO", "centrob"), Pa(f"{esc(obra)}: prazo, custo e qualidade", "centro"), Spacer(1, 1 * cm),
          Pa(f"Relatório gerencial mensal da obra {esc(obra)}, com data de status em {ref:%d/%m/%Y}, referente a {mes_ano}. "
             "Apresenta o acompanhamento de prazo, custo e qualidade a partir dos dados lançados no sistema Custo Plano. "
             "Elaborado conforme a ABNT NBR 10719.", "natureza"),
          Spacer(1, 7 * cm), Pa("Guarulhos", "centro"), Pa(mes_ano, "centro"), PageBreak()]

    # ------------------------------------------------------------------ resumo
    frases = [f"Este relatório apresenta o acompanhamento mensal da obra {esc(obra)}, com data de status em {ref:%d/%m/%Y}, "
              "nos três pilares de gestão: prazo, custo e qualidade."]
    if p:
        frases.append(f"O avanço físico real é de {_br(real)}%, contra {_br(prev)}% previstos na linha de base, "
                      f"um desvio de {'+' if desv >= 0 else ''}{_br(desv)} pontos percentuais.")
    if ppc:
        frases.append(f"O PPC da semana de {_d(ppc['semana'])} foi de {ppc['ppc']}%.")
    if idc is not None:
        frases.append(f"No custo, o índice de desempenho de custo (IDC) é {_br(idc, 2)}"
                      + (f" e o índice de desempenho de prazo (IDP) é {_br(idp, 2)}." if idp is not None else "."))
    elif custos:
        frases.append("O orçamento está importado, mas ainda não há custo realizado lançado.")
    if indicadores is not None:
        frases.append(f"Na qualidade, foram registradas {qual.get('inspecoes_30d', 0)} inspeções nos últimos 30 dias "
                      f"e há {ind.get('ncs_abertas', 0)} não conformidades abertas.")
    frases.append("A conclusão reúne os pontos de atenção e os próximos passos recomendados.")
    H += [Pa("RESUMO", "tsn"), Pa(" ".join(frases), "resumo"), Spacer(1, 14),
          Pa("<b>Palavras-chave:</b> prazo. custo. qualidade. avanço físico. gestão de obras.", "resumo"), PageBreak()]

    # ----------------------------------------------------------------- sumário
    toc = TableOfContents()
    toc.levelStyles = [est("toc1", fontName="Helvetica-Bold", leading=18, leftIndent=0, firstLineIndent=0),
                       est("toc2", leading=18, leftIndent=0, firstLineIndent=0)]
    toc.dotsMinLevel = 0
    H += [Pa("SUMÁRIO", "tsn"), toc, NextPageTemplate("texto"), PageBreak()]

    # -------------------------------------------------------------- introdução
    fontes = []
    if p:
        fontes.append("o cronograma importado e a medição de avanço físico")
    if plano:
        fontes.append("o plano de ataque")
    if custos:
        fontes.append("o orçamento e os custos lançados")
    if indicadores is not None:
        fontes.append("o planejamento de curto prazo e as fichas de verificação de serviço (FVS)")
    H += [Pa("1 INTRODUÇÃO", "h1"),
          Pa(f"Este relatório tem como objetivo apresentar a situação da obra {esc(obra)} em {ref:%d/%m/%Y}, "
             "data de status da medição, para apoiar as decisões da obra e do cliente. "
             "Prazo, custo e qualidade são analisados em conjunto, porque um desvio em um deles afeta os outros dois.", "txt"),
          Pa(("Os dados vêm do sistema Custo Plano: " + ", ".join(fontes[:-1]) + (" e " if len(fontes) > 1 else "") + fontes[-1] + ". ")
             if fontes else "Os dados vêm do sistema Custo Plano. ", "txt"),
          Pa("Este documento tem caráter gerencial: organiza e analisa as informações de planejamento e controle, "
             "sem substituir a responsabilidade técnica da obra.", "txt"),
          Pa("2 DESENVOLVIMENTO", "h1"), Pa("2.1 Prazo", "h2")]

    # ------------------------------------------------------------------- prazo
    if p:
        linhas = [["Avanço físico real", f"{_br(real)}%", f"medição anterior: {_br(ant)}%"],
                  ["Avanço no período", f"{'+' if real - ant >= 0 else ''}{_br(real - ant)} p.p.", "desde a medição anterior"],
                  ["Previsto na linha de base", f"{_br(prev)}%", f"em {data_status:%d/%m/%Y}"],
                  ["Desvio", Pa(f"<font color='{cor_desv(desv)}'><b>{'+' if desv >= 0 else ''}{_br(desv)} p.p.</b></font>", "tr"),
                   "real menos previsto"]]
        if ppc:
            linhas.append(["PPC (planejamento semanal)", Pa(f"<font color='{cor_desv(ppc['ppc'], 80)}'><b>{ppc['ppc']}%</b></font>", "tr"),
                           f"semana de {_d(ppc['semana'])}: {ppc['ok']} de {ppc['ok'] + ppc['nok']} pacotes cumpridos (meta: 80%)"])
        H.append(Pa(f"A Tabela 1 resume o avanço físico da obra na data de status. O avanço real está "
                    f"{'acima' if desv > 0.05 else 'abaixo' if desv < -0.05 else 'em linha com'} "
                    f"{'do previsto' if abs(desv) > 0.05 else 'o previsto'} na linha de base.", "txt"))
        add(H, tabela("Indicadores de prazo", ["Indicador", "Valor", "Observação"], linhas, [5.2 * cm, 2.8 * cm, W - 8 * cm],
                      direita=(1,)))

        # Curva S
        prevista = curva_prevista(p, criterio, data_status)
        reais = sorted({(dt.datetime.fromisoformat(h["data_status"][:10]), float(h["avanco_real"])) for h in historico})
        reais = [r for r in reais if r[0].date() != data_status.date()] + [(data_status, real)]
        reais.sort()
        if prevista:
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
            d.add(String(xs + 3, B + 4, f"status {data_status:%d/%m}", fontName="Helvetica-Bold", fontSize=8, fillColor=NAVY2))
            d.add(Rect(L, dh - 8, 14, 1.3, fillColor=CINZA, strokeColor=None))
            d.add(String(L + 18, dh - 10, "Previsto (linha de base)", fontName="Helvetica", fontSize=8, fillColor=CINZA))
            d.add(Rect(L + 130, dh - 8.5, 14, 2.2, fillColor=NAVY, strokeColor=None))
            d.add(String(L + 148, dh - 10, "Real (medições fechadas e atual)", fontName="Helvetica", fontSize=8, fillColor=CINZA))
            H.append(Pa("A Figura 1 compara o avanço físico acumulado real com o previsto na linha de base, mês a mês.", "txt"))
            H.append(KeepTogether([legenda("Figura", "Curva S do avanço físico acumulado"), d, fonte_dados()]))

        # avanço por etapa
        nmin = min(t.nivel for t in p.tarefas)
        etapas = [t for t in p.tarefas if t.nivel == nmin]
        if len(etapas) < 3:
            etapas = [t for t in p.tarefas if t.nivel <= nmin + 1]
        etapas = etapas[:28]
        linhas = []
        for t in etapas:
            a = A[t.uid]
            if t.filhos:
                pr, pa_, pp = p.pct(a, "r"), p.pct(a, "b"), p.pct(a, "p")
            else:
                pr, pa_, pp = float(atual.get(t.uid, 0)), float(anterior.get(t.uid, 0)), p.previsto(t, data_status)
            dv = pr - pp
            linhas.append([t.eap, Pa(("<b>%s</b>" if t.nivel == nmin else "%s") % esc(t.nome)),
                           f"{_br(a['w'] / tot['w'] * 100 if tot['w'] else 0)}%", f"{_br(pa_)}%", f"{_br(pr)}%",
                           f"{_br(pp)}%", Pa(f"<font color='{cor_desv(dv)}'>{'+' if dv >= 0 else ''}{_br(dv)}</font>", "tr")])
        H.append(Pa(f"A Tabela {ntab[0] + 1} detalha o avanço por etapa: peso no avanço total, avanço na medição anterior, "
                    "avanço atual, previsto e desvio em pontos percentuais.", "txt"))
        add(H, tabela("Avanço físico por etapa", ["EAP", "Etapa", "Peso", "Anterior", "Atual", "Previsto", "Desvio p.p."], linhas,
                      [1.4 * cm, W - 11.4 * cm, 1.7 * cm, 1.8 * cm, 1.7 * cm, 2 * cm, 2.8 * cm], direita=(2, 3, 4, 5, 6)))

        # maiores atrasos
        folhas = []
        for t in p.tarefas:
            if t.filhos or t.marco:
                continue
            pr, pp = float(atual.get(t.uid, 0)), p.previsto(t, data_status)
            if pp - pr > 0.5:
                folhas.append((A[t.uid]["w"] * (pp - pr), t, pr, pp))
        folhas.sort(key=lambda x: -x[0])
        if folhas:
            linhas = [[t.eap, Pa(esc(t.nome)), _d(t.lb_termino or t.termino), f"{_br(pp)}%", f"{_br(pr)}%",
                       Pa(f"<font color='#8E1616'>-{_br(pp - pr)}</font>", "tr")] for _, t, pr, pp in folhas[:12]]
            H.append(Pa(f"A Tabela {ntab[0] + 1} lista as tarefas com maior impacto no atraso, ordenadas pelo peso no avanço "
                        "multiplicado pela diferença entre o previsto e o real.", "txt"))
            add(H, tabela("Tarefas com maior impacto no atraso", ["EAP", "Tarefa", "Término LB", "Previsto", "Atual", "Desvio p.p."],
                          linhas, [1.6 * cm, W - 10.4 * cm, 2.4 * cm, 2 * cm, 1.8 * cm, 2.6 * cm], direita=(3, 4, 5)))
    elif not plano:
        H.append(Pa("O cronograma da obra ainda não foi importado no sistema, por isso o avanço físico não é apresentado.", "txt"))

    # plano de ataque
    if plano:
        r = resumo_plano(plano)
        tm = plano.get("termino") or {}
        H.append(Pa(f"O plano de ataque ({esc(plano.get('titulo', ''))}) acompanha os pacotes de serviço por pavimento. "
                    f"A Tabela {ntab[0] + 1} resume a execução e a aderência até a data base.", "txt"))
        linhas = [["Pacotes executados", f"{round(r['ok'] / r['tot'] * 100) if r['tot'] else 0}%", f"{r['ok']} de {r['tot']} (serviço x pavimento)"],
                  ["Aderência até a data base", f"{round(r['prev_ok'] / r['prev'] * 100) if r['prev'] else 0}%",
                   f"{r['prev_ok']} de {r['prev']} até {_d(r['db'])}"],
                  ["Pacotes atrasados", Pa(f"<font color='{'#8E1616' if r['late'] else '#002455'}'><b>{r['late']}</b></font>", "tr"),
                   "data passou e sem execução confirmada"]]
        if tm:
            linhas.append(["Término da obra", _d(tm.get("atual") or tm.get("lb")), f"linha de base {_d(tm.get('lb'))} ({_dias(tm.get('var'))})"])
        add(H, tabela("Plano de ataque", ["Indicador", "Valor", "Observação"], linhas, [5.2 * cm, 2.8 * cm, W - 8 * cm], direita=(1,)))
        if plano.get("marcos"):
            add(H, tabela("Marcos da obra", ["Marco", "Linha de base", "Mês anterior", "Atual", "Var. LB", "Var. mês"],
                          [[m["nome"], _d(m["lb"]), _d(m["anterior"]), _d(m["atual"]), _dias(m["var_lb"]), _dias(m["var_mes"])]
                           for m in plano["marcos"]],
                          [W - 10.6 * cm, 2.2 * cm, 2.2 * cm, 2 * cm, 2.1 * cm, 2.1 * cm], direita=(4, 5)))

    # ------------------------------------------------------------------- custo
    H.append(Pa("2.2 Custo", "h2"))
    if custos:
        H.append(Pa("O custo é acompanhado pela análise de valor agregado: o valor agregado (VA) é o orçado da parte executada, "
                    "e o IDC é a razão entre o valor agregado e o custo realizado. IDC abaixo de 1,00 indica que a obra gasta "
                    f"mais do que o orçado para o que já executou. A Tabela {ntab[0] + 1} apresenta os totais.", "txt"))
        linhas = [["Orçamento (custo direto)", brl(T["bac"]), f"com BDI: {brl(T['venda'])}"],
                  ["Previsto até a data de status", brl(T["vp"]), ""],
                  ["Valor agregado", brl(T["va"]), ""],
                  ["Custo realizado", brl(T["cr"]), f"{_br(T['cr'] / T['bac'] * 100 if T['bac'] else 0)}% do orçamento"],
                  ["IDC", Pa(f"<font color='{cor_desv(idc or 1, 0.98)}'><b>{'-' if idc is None else _br(idc, 2)}</b></font>", "tr"),
                   "sem custo lançado" if idc is None else "valor agregado / custo realizado"],
                  ["IDP", "-" if idp is None else _br(idp, 2), "valor agregado / previsto"],
                  ["Estimativa no término", brl(T.get("eac")), "" if T.get("vac") is None else f"desvio: {brl(T['vac'])}"]]
        add(H, tabela("Indicadores de custo", ["Indicador", "Valor", "Observação"], linhas, [5.2 * cm, 3.4 * cm, W - 8.6 * cm],
                      direita=(1,)))
        linhas = []
        for e in custos["etapas"]:
            cv = e["va"] - e["cr"]
            linhas.append([e["codigo"], Pa(esc(e["nome"])), mil(e["orcado"]), mil(e["vp"]), mil(e["va"]), mil(e["cr"]),
                           Pa(f"<font color='{cor_desv(cv, -500)}'>{'-' if cv < -500 else '+'}{mil(abs(cv))}</font>", "tr"),
                           "-" if e["idc"] is None else _br(e["idc"], 2)])
        cvt = T["va"] - T["cr"]
        linhas.append(["", Pa("<b>Total</b>"), mil(T["bac"]), mil(T["vp"]), mil(T["va"]), mil(T["cr"]),
                       Pa(f"<b>{'-' if cvt < -500 else '+'}{mil(abs(cvt))}</b>", "tr"), "-" if idc is None else _br(idc, 2)])
        H.append(Pa(f"A Tabela {ntab[0] + 1} mostra o custo por etapa, em milhares de reais.", "txt"))
        add(H, tabela("Custo por etapa (R$ mil)", ["Cód.", "Etapa", "Orçado", "Previsto", "Agregado", "Realizado", "VA - CR", "IDC"], linhas,
                      [1.1 * cm, W - 13.1 * cm, 2 * cm, 2 * cm, 2 * cm, 2 * cm, 2.6 * cm, 1.4 * cm],
                      direita=(2, 3, 4, 5, 6, 7), total=True))
        serie = custos.get("serie") or []
        if 0 < len(serie) <= 24:
            # um mês por linha: cabe qualquer duração e qualquer valor sem quebrar os números
            mes = lambda v: mil(v) if v else ""  # noqa: E731
            add(H, tabela("Cronograma financeiro (R$ mil)", ["Mês", "Previsto no mês", "Previsto acumulado", "Realizado no mês",
                                                            "Realizado acumulado"],
                          [[f"{MESES[int(s_['mes'][5:7]) - 1]}/{s_['mes'][:4]}", mes(s_["previsto"]), mes(s_["previsto_acum"]),
                            mes(s_["realizado"]), mes(s_["realizado_acum"])] for s_ in serie],
                          [W - 13.2 * cm, 3.3 * cm, 3.3 * cm, 3.3 * cm, 3.3 * cm], direita=(1, 2, 3, 4)))
    else:
        H.append(Pa("O orçamento da obra ainda não foi importado no sistema, por isso o custo não é apresentado.", "txt"))

    # --------------------------------------------------------------- qualidade
    H.append(Pa("2.3 Qualidade", "h2"))
    if indicadores is not None:
        ap = qual.get("aprovacao_1a_pct")
        servs = qual.get("servicos_inspecionados") or []
        H.append(Pa("A qualidade é acompanhada pelas fichas de verificação de serviço (FVS) e pelas não conformidades (NC) "
                    f"registradas no sistema. A Tabela {ntab[0] + 1} considera os últimos 30 dias até a geração do relatório.", "txt"))
        linhas = [["Inspeções realizadas", str(qual.get("inspecoes_30d", 0)),
                   (", ".join(esc(s) for s in servs[:6]) + ("..." if len(servs) > 6 else "")) if servs else "nenhum serviço inspecionado"],
                  ["Reinspeções", str(qual.get("reinspecoes_30d", 0)), ""],
                  ["Aprovação na 1ª inspeção", "-" if ap is None else f"{ap}%", ""],
                  ["NCs abertas", Pa(f"<font color='{'#8E1616' if ind.get('ncs_abertas') else '#002455'}'><b>{ind.get('ncs_abertas', 0)}</b></font>", "tr"),
                   f"{ind.get('ncs_altas', 0)} de gravidade alta, {qual.get('ncs_sem_responsavel', 0)} sem responsável"],
                  ["NCs com prazo vencido", Pa(f"<font color='{'#8E1616' if ind.get('ncs_vencidas') else '#002455'}'><b>{ind.get('ncs_vencidas', 0)}</b></font>", "tr"), ""],
                  ["NCs fechadas", str(qual.get("ncs_fechadas_30d", 0)),
                   "" if qual.get("dias_medio_fechamento") is None else f"prazo médio de fechamento: {_br(qual['dias_medio_fechamento'])} dias"]]
        add(H, tabela("Indicadores de qualidade (últimos 30 dias)", ["Indicador", "Valor", "Observação"], linhas,
                      [5.2 * cm, 2.2 * cm, W - 7.4 * cm], direita=(1,)))
        por = qual.get("ncs_abertas_por_servico") or {}
        if por:
            add(H, tabela("Não conformidades abertas por serviço", ["Serviço", "NCs abertas"],
                          [[esc(k), str(v)] for k, v in sorted(por.items(), key=lambda x: -x[1])], [W - 3.5 * cm, 3.5 * cm], direita=(1,)))
    else:
        H.append(Pa("Os indicadores de qualidade não estão disponíveis para esta obra.", "txt"))

    # --------------------------------------------------------------- conclusão
    H.append(Pa("3 CONCLUSÃO", "h1"))
    itens = []
    if p:
        if desv < -5:
            itens.append(f"<b>Prazo:</b> a obra está {_br(-desv)} p.p. atrás do previsto. Recomenda-se rever o plano de ataque "
                         "das tarefas de maior impacto no atraso e confirmar com a obra os recursos para recuperar o ritmo.")
        elif desv < -0.05:
            itens.append(f"<b>Prazo:</b> desvio de {_br(desv)} p.p., dentro da tolerância de 5 p.p.; manter o acompanhamento semanal.")
        else:
            itens.append(f"<b>Prazo:</b> o avanço real está em linha com o previsto ou à frente ({'+' if desv >= 0 else ''}{_br(desv)} p.p.).")
    if ppc and ppc["ppc"] < 80:
        itens.append(f"<b>Planejamento semanal:</b> PPC de {ppc['ppc']}%, abaixo da meta de 80%. Registrar as causas de não "
                     "cumprimento de cada pacote para atacar as que se repetem.")
    if idc is not None:
        if idc < 0.98:
            itens.append(f"<b>Custo:</b> IDC de {_br(idc, 2)}: a obra gasta mais do que o orçado para o que executou. "
                         "Conferir os lançamentos de custo realizado e as etapas com maior diferença entre agregado e realizado.")
        else:
            itens.append(f"<b>Custo:</b> IDC de {_br(idc, 2)}, dentro do orçado para o que foi executado.")
    elif custos:
        itens.append("<b>Custo:</b> lançar o custo realizado na tela Custos para que o IDC seja calculado.")
    if indicadores is not None:
        if not qual.get("inspecoes_30d"):
            itens.append("<b>Qualidade:</b> não houve inspeção registrada nos últimos 30 dias. Programar as FVS dos serviços em execução.")
        if ind.get("ncs_vencidas"):
            itens.append(f"<b>Qualidade:</b> {ind['ncs_vencidas']} não conformidades com prazo vencido; cobrar os responsáveis.")
        if qual.get("inspecoes_30d") and not ind.get("ncs_vencidas"):
            n = ind.get("ncs_abertas", 0)
            itens.append(f"<b>Qualidade:</b> {qual['inspecoes_30d']} inspeções no período, sem não conformidades vencidas"
                         + (f"; acompanhar o fechamento das {n} abertas." if n else " nem abertas."))
    if not itens:
        itens.append("Não há dados suficientes para conclusões nesta data de status.")
    H.append(Pa("Os principais pontos desta data de status, por pilar, são:", "txt"))
    for i, t in enumerate(itens):
        H.append(Pa(f"{'abcdefghij'[i]})&nbsp;&nbsp;{t.rstrip('.')}{';' if i < len(itens) - 1 else '.'}", "item"))
    obs = Table([[""]], colWidths=[W], rowHeights=[3 * cm])
    obs.setStyle(TableStyle([("BOX", (0, 0), (-1, -1), 0.6, LINHA)]))
    ass = Table([["", "", ""], [Pa("Responsável pelo planejamento", "small"), Pa("Engenheiro da obra", "small"), Pa("Cliente / gerenciadora", "small")]],
                colWidths=[W / 3] * 3, rowHeights=[2 * cm, None])
    ass.setStyle(TableStyle([("LINEBELOW", (0, 0), (0, 0), 0.7, NAVY), ("LINEBELOW", (1, 0), (1, 0), 0.7, NAVY),
                             ("LINEBELOW", (2, 0), (2, 0), 0.7, NAVY), ("LEFTPADDING", (0, 0), (-1, -1), 6),
                             ("RIGHTPADDING", (0, 0), (-1, -1), 6)]))
    H.append(KeepTogether([Spacer(1, 10), Pa("Observações:", "t"), obs, Spacer(1, 6), ass]))

    # ------------------------------------------------------------- referências
    H += [PageBreak(), Pa("REFERÊNCIAS", "tsn"),
          Pa("ASSOCIAÇÃO BRASILEIRA DE NORMAS TÉCNICAS. <b>NBR 10719</b>: informação e documentação: relatório técnico e/ou "
             "científico: apresentação. Rio de Janeiro: ABNT, 2015.", "ref"),
          Pa(f"CUSTO PLANO CONSULTORIA. <b>Sistema Custo Plano</b>: dados da obra {esc(obra)}. Guarulhos, {ref.year}. "
             f"Dados extraídos em {gerado:%d/%m/%Y}.", "ref")]

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
