"""
Relatório mensal de avanço em PDF: resumo da medição, Curva S, avanço por etapa,
tarefas com maior atraso, plano de ataque (se importado) e campos de assinatura.

Usa só as fontes padrão do PDF (Helvetica), que funcionam no servidor sem instalar nada.
"""
from __future__ import annotations

import datetime as dt
import io

from cronograma import Projeto

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
          historico: list, plano: dict | None, obra: str, custos: dict | None = None) -> bytes:
    from reportlab.graphics.shapes import Circle, Drawing, Line, PolyLine, Rect, String
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.platypus import (BaseDocTemplate, Frame, KeepTogether, PageTemplate, Paragraph, Spacer,
                                    Table, TableStyle)

    NAVY, BLUE, LIGHT = colors.HexColor("#282B59"), colors.HexColor("#3D6A99"), colors.HexColor("#AFC3D9")
    MUTED, LINE, GROUND = colors.HexColor("#5E6485"), colors.HexColor("#D3DCDC"), colors.HexColor("#F3F6F6")
    GOOD, BAD = colors.HexColor("#1E6B45"), colors.HexColor("#9A3412")

    def est(nome, **k):
        base = dict(fontName="Helvetica", fontSize=9.5, leading=13, textColor=NAVY)
        base.update(k)
        return ParagraphStyle(nome, **base)

    S = {
        "h1": est("h1", fontName="Helvetica-Bold", fontSize=20, leading=24),
        "h2": est("h2", fontName="Helvetica-Bold", fontSize=13, leading=17, spaceBefore=10, spaceAfter=5),
        "sub": est("sub", fontSize=10.5, leading=14, textColor=MUTED, spaceAfter=8),
        "t": est("t", fontSize=8.3, leading=10.5),
        "tb": est("tb", fontName="Helvetica-Bold", fontSize=8.3, leading=10.5),
        "tr": est("tr", fontSize=8.3, leading=10.5, alignment=2),
        "th": est("th", fontName="Helvetica-Bold", fontSize=7.8, leading=10, textColor=colors.white),
        "thr": est("thr", fontName="Helvetica-Bold", fontSize=7.8, leading=10, textColor=colors.white, alignment=2),
        "k1": est("k1", fontSize=8, leading=10, textColor=MUTED),
        "k2": est("k2", fontName="Helvetica-Bold", fontSize=17, leading=21),
        "k3": est("k3", fontSize=7.8, leading=10, textColor=MUTED),
        "small": est("small", fontSize=7.8, leading=10, textColor=MUTED),
    }
    Pa = lambda t, s="t": Paragraph(t, S[s])  # noqa: E731

    def tabela(cab, linhas, larg, direita=()):
        dados = [[Pa(h, "thr" if i in direita else "th") for i, h in enumerate(cab)]]
        for ln in linhas:
            dados.append([c if not isinstance(c, str) else Pa(c, "tr" if i in direita else "t") for i, c in enumerate(ln)])
        t = Table(dados, colWidths=larg, repeatRows=1)
        estilo = [("BACKGROUND", (0, 0), (-1, 0), NAVY), ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                  ("LINEBELOW", (0, 1), (-1, -1), 0.4, LINE),
                  ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                  ("LEFTPADDING", (0, 0), (-1, -1), 4), ("RIGHTPADDING", (0, 0), (-1, -1), 4)]
        for i in range(2, len(dados), 2):
            estilo.append(("BACKGROUND", (0, i), (-1, i), GROUND))
        t.setStyle(TableStyle(estilo))
        return t

    def kpis(itens, largura):
        cel = [[Pa(a, "k1"), Pa(b, "k2"), Pa(c, "k3")] for a, b, c in itens]
        t = Table([cel], colWidths=[largura / len(itens)] * len(itens))
        t.setStyle(TableStyle([("BOX", (0, 0), (-1, -1), 0.6, LINE), ("INNERGRID", (0, 0), (-1, -1), 0.6, LINE),
                               ("VALIGN", (0, 0), (-1, -1), "TOP"), ("TOPPADDING", (0, 0), (-1, -1), 6),
                               ("BOTTOMPADDING", (0, 0), (-1, -1), 7), ("LEFTPADDING", (0, 0), (-1, -1), 7)]))
        return t

    def cabecalho(c, doc):
        w, h = A4
        c.saveState()
        c.setFillColor(NAVY)
        c.rect(0, h - 10 * mm, w, 10 * mm, fill=1, stroke=0)
        for i, (x, hh) in enumerate(((16, 2.4), (19, 4), (22, 5.6))):
            c.setFillColor(LIGHT if i < 2 else colors.white)
            c.rect(x * mm, h - 8 * mm, 2.3 * mm, hh * mm, fill=1, stroke=0)
        c.setFillColor(colors.white)
        c.setFont("Helvetica-Bold", 8.5)
        c.drawString(27 * mm, h - 6.4 * mm, "CUSTO PLANO ENGENHARIA")
        c.setFont("Helvetica", 8.5)
        c.drawRightString(w - 16 * mm, h - 6.4 * mm, "Relatório mensal de avanço  |  " + obra[:60])
        c.setFillColor(MUTED)
        c.setFont("Helvetica", 7.5)
        c.drawString(16 * mm, 9 * mm, f"Gerado em {dt.datetime.now():%d/%m/%Y %H:%M} pelo sistema Custo Plano.")
        c.drawRightString(w - 16 * mm, 9 * mm, f"Página {doc.page}")
        c.restoreState()

    buf = io.BytesIO()
    doc = BaseDocTemplate(buf, pagesize=A4, leftMargin=16 * mm, rightMargin=16 * mm, topMargin=17 * mm,
                          bottomMargin=16 * mm, title=f"Relatório mensal - {obra}", author="Custo Plano Engenharia")
    doc.addPageTemplates([PageTemplate(id="p", frames=[Frame(doc.leftMargin, doc.bottomMargin, doc.width, doc.height)],
                                       onPage=cabecalho)])
    W = doc.width
    H = []
    ref = data_status if p else dt.datetime.combine((plano and resumo_plano(plano)["db"]) or dt.date.today(), dt.time(17))
    H += [Pa("Relatório mensal de avanço", "h1"), Spacer(1, 3),
          Pa(f"{obra}  ·  data de status {ref:%d/%m/%Y}  ·  referência {MESES[ref.month - 1]}/{ref.year}", "sub")]

    if p:
        A = p.agregar(atual, anterior, data_status, criterio)
        tot = A["total"]
        real, prev, ant = p.pct(tot, "r"), p.pct(tot, "p"), p.pct(tot, "b")
        desv = real - prev
        H.append(kpis([
            ("Avanço físico real", f"{_br(real)}%", f"medição anterior {_br(ant)}%"),
            ("Avanço no período", f"{'+' if real - ant >= 0 else ''}{_br(real - ant)} p.p.", "desde a medição anterior"),
            ("Previsto (linha de base)", f"{_br(prev)}%", f"em {data_status:%d/%m/%Y}"),
            ("Desvio", f"<font color='{'#9A3412' if desv < -0.05 else '#1E6B45'}'>{'+' if desv >= 0 else ''}{_br(desv)} p.p.</font>",
             "real menos previsto"),
        ], W))

        # ---------------------------------------------------------- Curva S
        prevista = curva_prevista(p, criterio, data_status)
        reais = sorted({(dt.datetime.fromisoformat(h["data_status"][:10]), float(h["avanco_real"])) for h in historico})
        reais = [r for r in reais if r[0].date() != data_status.date()] + [(data_status, real)]
        reais.sort()
        if prevista:
            dw, dh, L, B, T, R = W, 62 * mm, 30, 22, 14, 8
            d = Drawing(dw, dh)
            x0, x1 = prevista[0][0], prevista[-1][0]
            span = max((x1 - x0).total_seconds(), 1)
            X = lambda t: L + (t - x0).total_seconds() / span * (dw - L - R)  # noqa: E731
            Y = lambda v: B + v / 100 * (dh - B - T)  # noqa: E731
            for v in (0, 25, 50, 75, 100):
                d.add(Line(L, Y(v), dw - R, Y(v), strokeColor=LINE, strokeWidth=0.5))
                d.add(String(L - 4, Y(v) - 3, f"{v}%", fontName="Helvetica", fontSize=7, fillColor=MUTED, textAnchor="end"))
            passo = max(1, len(prevista) // 12)
            for i, (t, _) in enumerate(prevista):
                if i % passo == 0:
                    d.add(String(X(t), 8, f"{MESES[t.month - 1]}/{str(t.year)[2:]}", fontName="Helvetica", fontSize=6.8,
                                 fillColor=MUTED, textAnchor="middle"))
            pts = []
            for t, v in prevista:
                pts += [X(t), Y(v)]
            d.add(PolyLine(pts, strokeColor=MUTED, strokeWidth=1.3, strokeDashArray=[4, 3]))
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
            d.add(Line(xs, B, xs, dh - T + 4, strokeColor=BLUE, strokeWidth=0.8, strokeDashArray=[2, 2]))
            d.add(String(xs + 3, dh - T + 1, f"status {data_status:%d/%m}", fontName="Helvetica-Bold", fontSize=7, fillColor=BLUE))
            d.add(Rect(L, dh - 8, 14, 1.3, fillColor=MUTED, strokeColor=None))
            d.add(String(L + 18, dh - 10, "Previsto (linha de base)", fontName="Helvetica", fontSize=7, fillColor=MUTED))
            d.add(Rect(L + 120, dh - 8.5, 14, 2.2, fillColor=NAVY, strokeColor=None))
            d.add(String(L + 138, dh - 10, "Real (medições fechadas + atual)", fontName="Helvetica", fontSize=7, fillColor=MUTED))
            H += [Pa("Curva S · avanço físico acumulado", "h2"), d]

        # ---------------------------------------------------- avanço por etapa
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
            linhas.append([t.eap, Pa(("<b>%s</b>" if t.nivel == nmin else "%s") % t.nome.replace("&", "&amp;").replace("<", "&lt;")),
                           f"{_br(a['w'] / tot['w'] * 100 if tot['w'] else 0)}%", f"{_br(pa_)}%", f"{_br(pr)}%",
                           f"{_br(pp)}%",
                           Pa(f"<font color='{'#9A3412' if dv < -0.05 else '#1E6B45'}'>{'+' if dv >= 0 else ''}{_br(dv)}</font>", "tr")])
        H += [Pa("Avanço por etapa", "h2"),
              tabela(["EAP", "Etapa", "Peso", "Anterior", "Atual", "Previsto", "Desvio p.p."], linhas,
                     [16 * mm, W - 106 * mm, 16 * mm, 18 * mm, 18 * mm, 18 * mm, 20 * mm], direita=(2, 3, 4, 5, 6))]

        # ------------------------------------------------- maiores atrasos
        folhas = []
        for t in p.tarefas:
            if t.filhos or t.marco:
                continue
            pr, pp = float(atual.get(t.uid, 0)), p.previsto(t, data_status)
            if pp - pr > 0.5:
                folhas.append((A[t.uid]["w"] * (pp - pr), t, pr, pp))
        folhas.sort(key=lambda x: -x[0])
        if folhas:
            linhas = [[t.eap, Pa(t.nome.replace("&", "&amp;").replace("<", "&lt;")), _d(t.lb_termino or t.termino),
                       f"{_br(pp)}%", f"{_br(pr)}%", Pa(f"<font color='#9A3412'>-{_br(pp - pr)}</font>", "tr")]
                      for _, t, pr, pp in folhas[:12]]
            H.append(KeepTogether([Pa("Tarefas com maior impacto no atraso", "h2"),
                                   tabela(["EAP", "Tarefa", "Término LB", "Previsto", "Atual", "Desvio p.p."], linhas,
                                          [16 * mm, W - 94 * mm, 20 * mm, 18 * mm, 18 * mm, 22 * mm], direita=(3, 4, 5)),
                                   Pa("Ordenadas pelo impacto no avanço ponderado (peso × diferença entre previsto e real).", "small")]))

    # ------------------------------------------------------- plano de ataque
    if plano:
        r = resumo_plano(plano)
        tm = plano.get("termino") or {}
        blocos = [Pa("Plano de ataque · " + plano.get("titulo", "").replace("&", "&amp;"), "h2"),
                  kpis([
                      ("Pacotes executados", f"{round(r['ok'] / r['tot'] * 100) if r['tot'] else 0}%", f"{r['ok']} de {r['tot']} (serviço x pavimento)"),
                      ("Aderência até a data base", f"{round(r['prev_ok'] / r['prev'] * 100) if r['prev'] else 0}%",
                       f"{r['prev_ok']} de {r['prev']} até {_d(r['db'])}"),
                      ("Pacotes atrasados", f"<font color='{'#9A3412' if r['late'] else '#1E6B45'}'>{r['late']}</font>", "data passou e sem ok"),
                      ("Término da obra", _d(tm.get("atual") or tm.get("lb")), f"linha de base {_d(tm.get('lb'))} ({_dias(tm.get('var'))})" if tm else "-"),
                  ], W)]
        if plano.get("marcos"):
            blocos += [Spacer(1, 6), tabela(["Marco", "Linha de base", "Mês anterior", "Atual", "Var. LB", "Var. mês"],
                                            [[m["nome"], _d(m["lb"]), _d(m["anterior"]), _d(m["atual"]), _dias(m["var_lb"]), _dias(m["var_mes"])]
                                             for m in plano["marcos"]],
                                            [W - 110 * mm, 22 * mm, 22 * mm, 22 * mm, 22 * mm, 22 * mm], direita=(4, 5))]
        H.append(KeepTogether(blocos))

    # ------------------------------------------------------------------ custos
    if custos:
        T = custos["totais"]
        brl = lambda v: "-" if v is None else "R$ " + f"{v:,.0f}".replace(",", ".")  # noqa: E731
        idc = T.get("idc")
        blocos = [Pa("Custos · orçado x realizado", "h2"),
                  kpis([
                      ("Orçamento (custo direto)", brl(T["bac"]), f"com BDI: {brl(T['venda'])}"),
                      ("Custo realizado", brl(T["cr"]), f"{_br(T['cr'] / T['bac'] * 100 if T['bac'] else 0)}% do orçamento"),
                      ("Valor agregado", brl(T["va"]), f"previsto até a data: {brl(T['vp'])}"),
                      ("IDC", f"<font color='{'#9A3412' if idc is not None and idc < 0.98 else '#1E6B45'}'>{'-' if idc is None else _br(idc, 2)}</font>",
                       f"estimativa no término {brl(T['eac'])}" if T.get("eac") else "sem custo lançado"),
                  ], W), Spacer(1, 6)]
        linhas = []
        for e in custos["etapas"]:
            cv = e["va"] - e["cr"]
            linhas.append([e["codigo"], Pa(e["nome"].replace("&", "&amp;")), brl(e["orcado"]), brl(e["vp"]), brl(e["va"]), brl(e["cr"]),
                           Pa(f"<font color='{'#9A3412' if cv < -0.5 else '#1E6B45'}'>{'-' if cv < 0 else '+'}{brl(abs(cv))[3:]}</font>", "tr"),
                           "-" if e["idc"] is None else _br(e["idc"], 2)])
        cvt = T["va"] - T["cr"]
        linhas.append(["", Pa("<b>Total</b>"), brl(T["bac"]), brl(T["vp"]), brl(T["va"]), brl(T["cr"]),
                       Pa(f"<b>{'-' if cvt < 0 else '+'}{brl(abs(cvt))[3:]}</b>", "tr"), "-" if idc is None else _br(idc, 2)])
        blocos.append(tabela(["Cód.", "Etapa", "Orçado", "Previsto", "Agregado", "Realizado", "VA - CR", "IDC"], linhas,
                             [12 * mm, W - 136 * mm, 22 * mm, 22 * mm, 22 * mm, 22 * mm, 22 * mm, 14 * mm], direita=(2, 3, 4, 5, 6, 7)))
        H.append(KeepTogether(blocos))
        serie = custos.get("serie") or []
        if 0 < len(serie) <= 14:
            mil = lambda v: _br(v / 1000, 1) if v else ""  # noqa: E731
            cab = ["R$ mil"] + [f"{MESES[int(s_['mes'][5:7]) - 1]}/{s_['mes'][2:4]}" for s_ in serie]
            larg = [34 * mm] + [(W - 34 * mm) / len(serie)] * len(serie)
            H.append(KeepTogether([Spacer(1, 6), Pa("Cronograma financeiro", "h2"), tabela(cab, [
                ["Previsto no mês"] + [mil(s_["previsto"]) for s_ in serie],
                ["Previsto acumulado"] + [mil(s_["previsto_acum"]) for s_ in serie],
                ["Realizado no mês"] + [mil(s_["realizado"]) for s_ in serie],
                ["Realizado acumulado"] + [mil(s_["realizado_acum"]) for s_ in serie],
            ], larg, direita=tuple(range(1, len(serie) + 1)))]))

    # ------------------------------------------------------------ assinaturas
    H += [Spacer(1, 10), Pa("Observações", "h2")]
    obs = Table([[""]], colWidths=[W], rowHeights=[26 * mm])
    obs.setStyle(TableStyle([("BOX", (0, 0), (-1, -1), 0.6, LINE)]))
    H.append(obs)
    ass = Table([["", "", ""], [Pa("Responsável pelo planejamento", "k1"), Pa("Engenheiro da obra", "k1"), Pa("Cliente / gerenciadora", "k1")]],
                colWidths=[W / 3] * 3, rowHeights=[20 * mm, None])
    ass.setStyle(TableStyle([("LINEBELOW", (0, 0), (0, 0), 0.7, NAVY), ("LINEBELOW", (1, 0), (1, 0), 0.7, NAVY),
                             ("LINEBELOW", (2, 0), (2, 0), 0.7, NAVY), ("LEFTPADDING", (0, 0), (-1, -1), 8),
                             ("RIGHTPADDING", (0, 0), (-1, -1), 8)]))
    H.append(KeepTogether([Spacer(1, 6), ass]))

    doc.build(H)
    return buf.getvalue()
