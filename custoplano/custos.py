"""
Custos da obra: orçamento com composições, cronograma financeiro e orçado × realizado.

Planilha (.xlsx) com as abas:
- Orçamento:   Código | Descrição | Unidade | Quantidade | Composição | Custo unitário | Tarefa (EAP) | Início | Término
               linhas sem quantidade e sem custo são títulos de etapa (ex.: "1.2  Fundações")
- Composições: Composição | Descrição da composição | Unidade | Tipo | Insumo | Unidade do insumo | Coeficiente | Preço unitário
               (uma linha por insumo; o código da composição pode ficar em branco nas linhas seguintes)
- Realizado:   Data | Código (item ou etapa) | Descrição | Fornecedor | Tipo | Valor          (opcional)
- Parâmetros:  BDI (%)                                                                         (opcional)

O controle de custo usa o custo direto (o BDI entra só no preço de venda).
O cálculo liga cada item a uma tarefa do cronograma (coluna Tarefa, ou pelo código/nome da etapa)
para distribuir o custo no tempo pela linha de base e medir o valor agregado pelo % físico.
"""
from __future__ import annotations

import datetime as dt
import io
import re
import unicodedata

import openpyxl


class ErroCustos(Exception):
    pass


TIPOS = {"mat": "Material", "mo": "Mão de obra", "eq": "Equipamento", "sv": "Serviço", "out": "Outros"}


def norm(v) -> str:
    if v is None:
        return ""
    s = unicodedata.normalize("NFKD", str(v)).encode("ascii", "ignore").decode()
    return re.sub(r"\s+", " ", s).strip().lower().rstrip(":").strip()


def tipo_de(v) -> str:
    t = norm(v)
    if not t:
        return "out"
    if t.startswith("mat") or t in ("m", "insumo"):
        return "mat"
    if "obra" in t or t in ("mo", "m.o.", "m.o", "mao-de-obra") or t.startswith("mao"):
        return "mo"
    if t.startswith("equip") or t in ("eq", "e"):
        return "eq"
    if t.startswith("serv") or t.startswith("empreit") or t in ("sv", "s", "terceiro", "terceiros"):
        return "sv"
    return "out"


def numero(v) -> float | None:
    if v is None or isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip().replace("R$", "").replace("%", "").strip()
    if not s:
        return None
    if "," in s:
        s = s.replace(".", "").replace(",", ".")
    elif re.fullmatch(r"-?\d{1,3}(\.\d{3})+", s):
        s = s.replace(".", "")
    try:
        return float(s)
    except ValueError:
        return None


def data(v) -> dt.date | None:
    if isinstance(v, dt.datetime):
        return v.date()
    if isinstance(v, dt.date):
        return v
    if isinstance(v, (int, float)) and 20000 < v < 80000:
        return (dt.datetime(1899, 12, 30) + dt.timedelta(days=float(v))).date()
    if isinstance(v, str):
        m = re.search(r"(\d{1,2})/(\d{1,2})/(\d{2,4})", v)
        if m:
            a = int(m[3])
            try:
                return dt.date(a + 2000 if a < 100 else a, int(m[2]), int(m[1]))
            except ValueError:
                return None
        m = re.search(r"(\d{4})-(\d{2})-(\d{2})", v)
        if m:
            return dt.date(int(m[1]), int(m[2]), int(m[3]))
    return None


def texto(v) -> str:
    if v is None:
        return ""
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return str(v).strip()


# ------------------------------------------------------------------ leitura
def _cabecalho(linhas, sinonimos: dict, obrigatorios: tuple, max_linhas=30):
    """Acha a linha de cabeçalho e devolve (índice da linha, {campo: coluna})."""
    for r, linha in enumerate(linhas[:max_linhas]):
        nomes = [norm(v) for v in linha]
        mapa = {}
        for campo, opcoes in sinonimos.items():
            for c, n in enumerate(nomes):
                if n and c not in mapa.values() and any(n == o or (len(o) > 3 and n.startswith(o)) for o in opcoes):
                    mapa[campo] = c
                    break
        if all(k in mapa for k in obrigatorios):
            return r, mapa
    return None, None


def _aba(wb, *chaves):
    for ws in wb.worksheets:
        t = norm(ws.title)
        if any(k in t for k in chaves):
            return ws
    return None


def _linhas(ws, max_row=5000, max_col=40):
    return [list(r) for r in ws.iter_rows(min_row=1, max_row=max_row, max_col=max_col, values_only=True)]


SIN_ORC = {
    "codigo": ("codigo", "cod", "item", "cod."),
    "descricao": ("descricao", "descricao do servico", "servico", "discriminacao"),
    "unidade": ("unidade", "un", "und", "unid"),
    "qtd": ("quantidade", "qtd", "quant", "qtde"),
    "comp": ("composicao", "cod composicao", "codigo da composicao", "codigo composicao", "comp"),
    "preco": ("custo unitario", "preco unitario", "valor unitario", "unitario", "pu", "preco unit"),
    "total": ("custo total", "valor total", "total", "preco total"),
    "tarefa": ("tarefa (eap)", "tarefa", "eap da tarefa", "eap tarefa", "eap", "atividade"),
    "inicio": ("inicio", "data inicio", "data de inicio"),
    "termino": ("termino", "fim", "data termino", "data de termino"),
}
SIN_COMP = {
    "comp": ("composicao", "codigo da composicao", "cod composicao", "codigo"),
    "desc_comp": ("descricao da composicao", "descricao composicao"),
    "un_comp": ("unidade da composicao", "unidade composicao"),
    "tipo": ("tipo", "classe", "categoria"),
    "insumo": ("insumo", "descricao do insumo", "descricao insumo"),
    "un": ("unidade do insumo", "unidade insumo", "unidade", "un"),
    "coef": ("coeficiente", "coef", "consumo", "indice"),
    "preco": ("preco unitario", "custo unitario", "valor unitario", "preco", "pu"),
}
SIN_REAL = {
    "data": ("data", "data do pagamento", "data de pagamento", "competencia"),
    "codigo": ("codigo", "item", "etapa", "cod"),
    "descricao": ("descricao", "historico"),
    "fornecedor": ("fornecedor", "favorecido", "empresa"),
    "tipo": ("tipo", "classe", "categoria"),
    "valor": ("valor", "valor pago", "total"),
}


def ler_custos(nome_arquivo: str, conteudo: bytes) -> dict:
    if not re.search(r"\.xls[xm]$", nome_arquivo or "", re.I):
        raise ErroCustos("Envie o orçamento em Excel (.xlsx). Baixe o modelo na tela Custos se precisar.")
    try:
        wb = openpyxl.load_workbook(io.BytesIO(conteudo), data_only=True, read_only=True)
    except Exception:
        raise ErroCustos("Não consegui abrir a planilha. Confira se é um arquivo .xlsx válido.")
    try:
        ws_orc = _aba(wb, "orcament") or wb.worksheets[0]
        L = _linhas(ws_orc)
        r0, m = _cabecalho(L, SIN_ORC, ("codigo", "descricao"))
        if r0 is None or ("qtd" not in m and "total" not in m):
            raise ErroCustos("Na aba Orçamento não encontrei o cabeçalho com Código, Descrição e Quantidade.")

        bdi = None
        ws_par = _aba(wb, "parametr")
        for linhas in ([_linhas(ws_par, 50, 10)] if ws_par else []) + [L[:r0]]:
            for linha in linhas:
                for c, v in enumerate(linha):
                    if "bdi" in norm(v):
                        for w in linha[c + 1:]:
                            n = numero(w)
                            if n is not None:
                                bdi = n / 100 if n > 1 else n
                                break
                    if bdi is not None:
                        break
                if bdi is not None:
                    break
            if bdi is not None:
                break

        g = lambda linha, k: linha[m[k]] if k in m and m[k] < len(linha) else None  # noqa: E731
        itens = []
        for linha in L[r0 + 1:]:
            cod, desc = texto(g(linha, "codigo")), texto(g(linha, "descricao"))
            if not cod and not desc:
                continue
            qtd, preco, total = numero(g(linha, "qtd")), numero(g(linha, "preco")), numero(g(linha, "total"))
            comp = texto(g(linha, "comp"))
            titulo = qtd is None and preco is None and not comp and not texto(g(linha, "unidade"))
            if norm(desc).startswith("total"):
                continue
            itens.append({
                "codigo": cod, "descricao": desc, "titulo": bool(titulo),
                "unidade": texto(g(linha, "unidade")), "qtd": qtd if qtd is not None else (1.0 if total and not titulo else 0.0),
                "comp": comp, "preco": preco if preco is not None else (total if (total and qtd in (None, 0)) else None),
                "total_planilha": total,
                "tarefa": texto(g(linha, "tarefa")),
                "inicio": (data(g(linha, "inicio")) or None) and data(g(linha, "inicio")).isoformat(),
                "termino": (data(g(linha, "termino")) or None) and data(g(linha, "termino")).isoformat(),
            })
        if not any(not i["titulo"] for i in itens):
            raise ErroCustos("A aba Orçamento não tem nenhum item com quantidade ou custo.")

        comps = {}
        ws_c = _aba(wb, "composic")
        if ws_c:
            LC = _linhas(ws_c)
            rc, mc = _cabecalho(LC, SIN_COMP, ("comp", "coef", "preco"))
            if rc is not None:
                gc = lambda linha, k: linha[mc[k]] if k in mc and mc[k] < len(linha) else None  # noqa: E731
                atual = None
                for linha in LC[rc + 1:]:
                    cod = texto(gc(linha, "comp"))
                    if cod:
                        atual = comps.setdefault(cod, {"codigo": cod, "descricao": texto(gc(linha, "desc_comp")),
                                                       "unidade": texto(gc(linha, "un_comp")), "insumos": []})
                        if not atual["descricao"]:
                            atual["descricao"] = texto(gc(linha, "desc_comp"))
                    coef, preco = numero(gc(linha, "coef")), numero(gc(linha, "preco"))
                    if atual is None or coef is None or preco is None:
                        continue
                    atual["insumos"].append({"tipo": tipo_de(gc(linha, "tipo")), "descricao": texto(gc(linha, "insumo")),
                                             "unidade": texto(gc(linha, "un")), "coef": coef, "preco": preco})

        realizado = []
        ws_r = _aba(wb, "realizad", "lancament", "despesa", "pagament")
        if ws_r:
            LR = _linhas(ws_r)
            rr, mr = _cabecalho(LR, SIN_REAL, ("data", "valor"))
            if rr is not None:
                gr = lambda linha, k: linha[mr[k]] if k in mr and mr[k] < len(linha) else None  # noqa: E731
                for linha in LR[rr + 1:]:
                    d, v = data(gr(linha, "data")), numero(gr(linha, "valor"))
                    if not d or v is None:
                        continue
                    realizado.append({"data": d.isoformat(), "codigo": texto(gr(linha, "codigo")),
                                      "descricao": texto(gr(linha, "descricao")), "fornecedor": texto(gr(linha, "fornecedor")),
                                      "tipo": tipo_de(gr(linha, "tipo")), "valor": v, "origem": "planilha"})
    finally:
        wb.close()

    faltando = sorted({i["comp"] for i in itens if i["comp"] and i["comp"] not in comps})
    return {"arquivo": nome_arquivo, "bdi": bdi or 0.0, "itens": itens, "composicoes": comps,
            "realizado": realizado, "avisos": ([f"Composições citadas no orçamento e não encontradas: {', '.join(faltando[:8])}"
                                                + ("…" if len(faltando) > 8 else "")] if faltando else [])}


# --------------------------------------------------------------- análise
def _mes(d: dt.date) -> str:
    return f"{d.year:04d}-{d.month:02d}"


def _meses(a: dt.date, b: dt.date) -> list[str]:
    out, y, m = [], a.year, a.month
    while (y, m) <= (b.year, b.month):
        out.append(f"{y:04d}-{m:02d}")
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


def _distribuir(total: float, ini: dt.date, fim: dt.date) -> dict[str, float]:
    """Valor distribuído por mês, proporcional aos dias corridos entre início e término."""
    if fim < ini:
        ini, fim = fim, ini
    dias = (fim - ini).days + 1
    out: dict[str, float] = {}
    d = ini
    while d <= fim:
        prox = dt.date(d.year + (d.month == 12), d.month % 12 + 1, 1)
        ate = min(fim, prox - dt.timedelta(days=1))
        out[_mes(d)] = out.get(_mes(d), 0.0) + total * ((ate - d).days + 1) / dias
        d = prox
    return out


def _fracao(ini: dt.date, fim: dt.date, em: dt.date) -> float:
    if em < ini:
        return 0.0
    if em >= fim:
        return 1.0
    return ((em - ini).days + 1) / ((fim - ini).days + 1)


def analisar(dados: dict, lancamentos: list, projeto=None, atual: dict | None = None,
             data_status: dt.date | None = None) -> dict:
    atual = atual or {}
    ds = data_status or dt.date.today()
    bdi = float(dados.get("bdi") or 0)
    comps = dados.get("composicoes") or {}

    # ------------------------------------------------ tarefas do cronograma
    por_eap, por_nome, pct = {}, {}, {}
    if projeto is not None:
        A = projeto.agregar(atual, {}, dt.datetime.combine(ds, dt.time(17)))
        for t in projeto.tarefas:
            por_eap[t.eap] = t
            por_nome.setdefault(norm(t.nome), t)
            pct[t.uid] = projeto.pct(A[t.uid], "r") if t.filhos else float(atual.get(t.uid, 0))

    def datas_tarefa(t):
        i, f = t.lb_inicio or t.inicio, t.lb_termino or t.termino
        return (i.date() if i else None), (f.date() if f else None)

    # ------------------------------------------------------------ etapas
    titulos = [i for i in dados["itens"] if i["titulo"]]
    nivel_min = min((i["codigo"].count(".") for i in titulos if i["codigo"]), default=0)
    etapas, ordem = {}, []

    def etapa_de(item, ultimo):
        cod = item["codigo"]
        melhor = None
        for t in titulos:
            if t["codigo"] and cod.startswith(t["codigo"] + ".") and t["codigo"].count(".") == nivel_min:
                melhor = t
        return melhor or ultimo

    itens_out, ultimo_titulo, ultimo_top = [], None, None
    tot_tipo = {k: 0.0 for k in TIPOS}
    sem_vinculo = {"n": 0, "valor": 0.0}
    meses_prev: dict[str, float] = {}
    for it in dados["itens"]:
        if it["titulo"]:
            ultimo_titulo = it
            if not it["codigo"] or it["codigo"].count(".") == nivel_min:
                ultimo_top = it
            continue
        et = etapa_de(it, ultimo_top or ultimo_titulo)
        chave = (et["codigo"] + " " + et["descricao"]).strip() if et else "Sem etapa"
        if chave not in etapas:
            etapas[chave] = {"codigo": et["codigo"] if et else "", "nome": et["descricao"] if et else "Sem etapa",
                             "orcado": 0.0, "vp": 0.0, "va": 0.0, "cr": 0.0, "meses": {}, "titulo": et}
            ordem.append(chave)
        E = etapas[chave]

        comp = comps.get(it["comp"]) if it["comp"] else None
        por_tipo = {k: 0.0 for k in TIPOS}
        insumos = []
        if comp:
            for ins in comp["insumos"]:
                sub = ins["coef"] * ins["preco"]
                por_tipo[ins["tipo"]] += sub
                insumos.append(dict(ins, subtotal=round(sub, 4)))
            unit = sum(por_tipo.values())
        else:
            unit = float(it["preco"] or 0)
            if not unit and it.get("total_planilha") and it["qtd"]:
                unit = it["total_planilha"] / it["qtd"]
            por_tipo["out"] = unit
        direto = unit * float(it["qtd"] or 0)
        total = direto  # controle de custo: custo direto (o BDI entra só no preço de venda)
        for k in TIPOS:
            tot_tipo[k] += por_tipo[k] * float(it["qtd"] or 0)

        # vínculo com o cronograma
        t = None
        if it["tarefa"]:
            t = por_eap.get(it["tarefa"]) or por_nome.get(norm(it["tarefa"]))
        if t is None and it["codigo"] in por_eap:
            t = por_eap[it["codigo"]]
        if t is None and et is not None:
            if et.get("tarefa"):
                t = por_eap.get(et["tarefa"]) or por_nome.get(norm(et["tarefa"]))
            t = t or por_eap.get(et["codigo"]) or por_nome.get(norm(et["descricao"]))
        ini = fim = None
        if t is not None:
            ini, fim = datas_tarefa(t)
        if not ini and it.get("inicio"):
            ini = dt.date.fromisoformat(it["inicio"])
            fim = dt.date.fromisoformat(it["termino"]) if it.get("termino") else ini
        fisico = pct.get(t.uid) if t is not None else None

        vp = va = 0.0
        if ini and fim:
            for mes, v in _distribuir(total, ini, fim).items():
                meses_prev[mes] = meses_prev.get(mes, 0.0) + v
                E["meses"][mes] = E["meses"].get(mes, 0.0) + v
            vp = total * _fracao(ini, fim, ds)
        if fisico is not None:
            va = total * fisico / 100
        if t is None:
            sem_vinculo["n"] += 1
            sem_vinculo["valor"] += total
        E["orcado"] += total
        E["vp"] += vp
        E["va"] += va
        itens_out.append({
            "codigo": it["codigo"], "descricao": it["descricao"], "unidade": it["unidade"], "qtd": it["qtd"],
            "comp": it["comp"], "comp_desc": comp["descricao"] if comp else "", "unit": unit, "direto": direto,
            "total": total, "venda": direto * (1 + bdi), "tipos": por_tipo, "insumos": insumos, "etapa": chave,
            "tarefa": (t.eap + " " + t.nome) if t is not None else "", "fisico": fisico,
            "inicio": ini.isoformat() if ini else None, "termino": fim.isoformat() if fim else None, "vp": vp, "va": va,
        })

    # --------------------------------------------------------- realizado
    todos = [dict(x, origem="planilha") for x in dados.get("realizado") or []] + list(lancamentos or [])
    cod_item = {i["codigo"]: i for i in itens_out if i["codigo"]}
    meses_real: dict[str, float] = {}
    nao_ligados = 0.0
    cr_total = cr_ate = 0.0
    lanc_out = []
    for lc in todos:
        d = dt.date.fromisoformat(lc["data"])
        v = float(lc["valor"])
        cod = (lc.get("codigo") or "").strip()
        chave = None
        if cod in cod_item:
            chave = cod_item[cod]["etapa"]
        else:
            for k in ordem:
                ek = etapas[k]["codigo"]
                if cod and ek and (cod == ek or cod.startswith(ek + ".")):
                    chave = k
                    break
            if chave is None and cod:
                chave = next((k for k in ordem if norm(etapas[k]["nome"]) == norm(cod)), None)
        if chave:
            etapas[chave]["cr"] += v
        else:
            nao_ligados += v
        meses_real[_mes(d)] = meses_real.get(_mes(d), 0.0) + v
        cr_total += v
        if d <= ds:
            cr_ate += v
        lanc_out.append(dict(lc, etapa=chave or "Sem etapa"))
    lanc_out.sort(key=lambda x: x["data"], reverse=True)

    # ------------------------------------------------ série mensal
    todos_meses = sorted(set(meses_prev) | set(meses_real))
    serie = []
    if todos_meses:
        a = dt.date.fromisoformat(todos_meses[0] + "-01")
        b = dt.date.fromisoformat(todos_meses[-1] + "-01")
        acp = acr = 0.0
        for mes in _meses(a, b):
            p_, r_ = meses_prev.get(mes, 0.0), meses_real.get(mes, 0.0)
            acp += p_
            acr += r_
            serie.append({"mes": mes, "previsto": p_, "realizado": r_, "previsto_acum": acp, "realizado_acum": acr})

    bac = sum(E["orcado"] for E in etapas.values())
    vp = sum(E["vp"] for E in etapas.values())
    va = sum(E["va"] for E in etapas.values())
    cr = cr_total
    idc = va / cr if cr > 0 else None
    idp = va / vp if vp > 0 else None
    eac = (cr + (bac - va) / idc) if idc else None
    etapas_out = []
    for k in ordem:
        E = etapas[k]
        etapas_out.append({"chave": k, "codigo": E["codigo"], "nome": E["nome"], "orcado": E["orcado"], "vp": E["vp"],
                           "va": E["va"], "cr": E["cr"], "idc": (E["va"] / E["cr"]) if E["cr"] > 0 else None,
                           "meses": E["meses"]})
    if nao_ligados:
        etapas_out.append({"chave": "Sem etapa", "codigo": "", "nome": "Lançamentos sem etapa", "orcado": 0.0, "vp": 0.0,
                           "va": 0.0, "cr": nao_ligados, "idc": None, "meses": {}})
    direto = sum(tot_tipo.values())
    return {
        "arquivo": dados.get("arquivo"), "bdi": bdi, "data_status": ds.isoformat(), "tem_cronograma": projeto is not None,
        "totais": {"bac": bac, "venda": bac * (1 + bdi), "direto": direto, "vp": vp, "va": va, "cr": cr, "cr_ate": cr_ate, "idc": idc, "idp": idp,
                   "eac": eac, "vac": (bac - eac) if eac is not None else None},
        "tipos": [{"tipo": k, "nome": TIPOS[k], "valor": v, "com_bdi": v * (1 + bdi)} for k, v in tot_tipo.items() if v],
        "etapas": etapas_out, "itens": itens_out, "serie": serie, "lancamentos": lanc_out,
        "sem_vinculo": sem_vinculo, "avisos": dados.get("avisos") or [],
    }


# -------------------------------------------------- modelo e exemplo
CAB_ORC = ["Código", "Descrição", "Unidade", "Quantidade", "Composição", "Custo unitário", "Tarefa (EAP)", "Início", "Término"]
CAB_COMP = ["Composição", "Descrição da composição", "Unidade da composição", "Tipo", "Insumo", "Unidade do insumo",
            "Coeficiente", "Preço unitário"]
CAB_REAL = ["Data", "Código (item ou etapa)", "Descrição", "Fornecedor", "Tipo", "Valor"]


def montar_planilha(itens: list, comps: list, realizado: list, bdi: float, instrucoes: bool = True) -> bytes:
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.worksheet.datavalidation import DataValidation

    wb = openpyxl.Workbook()
    azul, cinza = PatternFill("solid", fgColor="282B59"), PatternFill("solid", fgColor="E3EAE9")
    branco = Font(bold=True, color="FFFFFF")

    def aba(ws, cab, larg):
        ws.append(cab)
        for c in ws[1]:
            c.fill, c.font = azul, branco
            c.alignment = Alignment(vertical="center", wrap_text=True)
        ws.row_dimensions[1].height = 30
        for i, w in enumerate(larg, start=1):
            ws.column_dimensions[ws.cell(1, i).column_letter].width = w
        ws.freeze_panes = "A2"

    ws = wb.active
    ws.title = "Orçamento"
    aba(ws, CAB_ORC, [10, 46, 9, 12, 13, 14, 13, 12, 12])
    for it in itens:
        ws.append(it)
        r = ws.max_row
        if it[3] in (None, "") and it[5] in (None, "") and it[4] in (None, ""):
            for c in ws[r]:
                c.font, c.fill = Font(bold=True), cinza
        for col in (8, 9):
            ws.cell(r, col).number_format = "dd/mm/yyyy"
        ws.cell(r, 6).number_format = '#,##0.00'

    wc = wb.create_sheet("Composições")
    aba(wc, CAB_COMP, [13, 40, 11, 14, 38, 10, 12, 13])
    dv = DataValidation(type="list", formula1='"Material,Mão de obra,Equipamento,Serviço,Outros"', allow_blank=True)
    wc.add_data_validation(dv)
    dv.add("D2:D5000")
    for c in comps:
        wc.append(c)
        wc.cell(wc.max_row, 8).number_format = '#,##0.00'

    wr = wb.create_sheet("Realizado")
    aba(wr, CAB_REAL, [12, 18, 40, 28, 14, 14])
    for x in realizado:
        wr.append(x)
        wr.cell(wr.max_row, 1).number_format = "dd/mm/yyyy"
        wr.cell(wr.max_row, 6).number_format = '#,##0.00'

    wp = wb.create_sheet("Parâmetros")
    wp.append(["BDI (%)", round(bdi * 100, 2)])
    wp["A1"].font = Font(bold=True)
    wp.column_dimensions["A"].width = 16
    if instrucoes:
        linhas = [
            "", "Como preencher",
            "Orçamento: uma linha por item. Linhas só com Código e Descrição (sem quantidade) são títulos de etapa (ex.: 1.2 Fundações).",
            "Composição: código da composição (aba Composições). Sem composição, preencha o Custo unitário.",
            "Tarefa (EAP): EAP da tarefa no MS Project (ex.: 1.3.2). Se ficar vazio, o sistema usa o código ou o nome da etapa.",
            "Início/Término: só se o item não estiver ligado ao cronograma.",
            "Composições: uma linha por insumo. Tipo = Material, Mão de obra, Equipamento, Serviço ou Outros.",
            "Custo unitário do item = soma de coeficiente × preço de cada insumo. O BDI é aplicado sobre o custo direto.",
            "Realizado (opcional): custos já pagos. Código = código do item ou da etapa. Também dá para lançar pelo sistema.",
        ]
        for t in linhas:
            wp.append([t])
        wp["A3"].font = Font(bold=True, size=12)
        wp.column_dimensions["A"].width = 120
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def modelo() -> bytes:
    itens = [
        ["1", "SERVIÇOS PRELIMINARES", None, None, None, None, None, None, None],
        ["1.1", "Locação da obra", "m²", 420, "C-001", None, "1.1.2", None, None],
        ["1.2", "Placa de obra", "m²", 6, None, 310.0, None, dt.date(2026, 1, 5), dt.date(2026, 1, 9)],
        ["2", "ESTRUTURA", None, None, None, None, None, None, None],
        ["2.1", "Concreto armado fck 30 MPa", "m³", 60, "C-002", None, "1.3.1", None, None],
    ]
    comps = [
        ["C-001", "Locação convencional de obra", "m²", "Mão de obra", "Carpinteiro", "h", 0.06, 28.0],
        [None, None, None, "Mão de obra", "Servente", "h", 0.06, 22.5],
        [None, None, None, "Material", "Pontalete e tábua", "m", 0.35, 9.8],
        ["C-002", "Concreto armado fck 30 MPa (forma, armação e lançamento)", "m³", "Material", "Concreto usinado fck 30", "m³", 1.03, 540.0],
        [None, None, None, "Material", "Aço CA-50", "kg", 105, 8.9],
        [None, None, None, "Material", "Forma de compensado", "m²", 8, 62.0],
        [None, None, None, "Mão de obra", "Carpinteiro", "h", 7, 28.0],
        [None, None, None, "Mão de obra", "Armador", "h", 6, 28.0],
        [None, None, None, "Equipamento", "Bomba de concreto", "m³", 1, 45.0],
    ]
    real = [[dt.date(2026, 1, 20), "1.1", "Equipe de locação - medição 1", "Empreiteira Alfa", "Mão de obra", 1520.0]]
    return montar_planilha(itens, comps, real, 0.25)


COMP_EXEMPLO = [
    ("C-CANT", "Canteiro de obras (instalações provisórias)", "vb", [
        ("Serviço", "Locação de contêineres (mês)", "mês", 3, 2800.0), ("Mão de obra", "Servente", "h", 160, 22.5),
        ("Material", "Tapume de chapa compensada", "m²", 120, 48.0)]),
    ("C-LOC", "Locação convencional de obra", "m²", [
        ("Mão de obra", "Carpinteiro", "h", 0.06, 28.0), ("Mão de obra", "Servente", "h", 0.06, 22.5),
        ("Material", "Pontalete e tábua", "m", 0.35, 9.8)]),
    ("C-EST", "Estaca hélice contínua D40", "m", [
        ("Serviço", "Perfuração hélice contínua", "m", 1, 92.0), ("Material", "Concreto usinado fck 30", "m³", 0.14, 520.0),
        ("Material", "Aço CA-50", "kg", 3.2, 8.9)]),
    ("C-BLO", "Blocos e baldrames em concreto armado", "m³", [
        ("Material", "Concreto usinado fck 30", "m³", 1.05, 520.0), ("Material", "Aço CA-50", "kg", 90, 8.9),
        ("Material", "Forma de compensado", "m²", 6, 62.0), ("Mão de obra", "Pedreiro", "h", 6, 28.0),
        ("Mão de obra", "Servente", "h", 8, 22.5)]),
    ("C-ESTR", "Estrutura de concreto armado (pilares, vigas e laje)", "m³", [
        ("Material", "Concreto usinado fck 30", "m³", 1.03, 540.0), ("Material", "Aço CA-50", "kg", 105, 8.9),
        ("Material", "Forma de compensado", "m²", 8, 62.0), ("Mão de obra", "Carpinteiro", "h", 7, 28.0),
        ("Mão de obra", "Armador", "h", 6, 28.0), ("Mão de obra", "Servente", "h", 10, 22.5),
        ("Equipamento", "Bomba de concreto", "m³", 1, 45.0)]),
    ("C-ALV", "Alvenaria de bloco cerâmico 14 cm", "m²", [
        ("Material", "Bloco cerâmico 14x19x29", "un", 13.5, 2.35), ("Material", "Argamassa de assentamento", "m³", 0.012, 480.0),
        ("Mão de obra", "Pedreiro", "h", 0.75, 28.0), ("Mão de obra", "Servente", "h", 0.45, 22.5)]),
    ("C-HID", "Instalações hidrossanitárias (por pavimento)", "pav", [
        ("Material", "Tubos, conexões e registros", "vb", 1, 9800.0), ("Mão de obra", "Encanador", "h", 160, 30.0),
        ("Mão de obra", "Ajudante", "h", 160, 22.5)]),
    ("C-ELE", "Instalações elétricas (por pavimento)", "pav", [
        ("Material", "Cabos, eletrodutos e caixas", "vb", 1, 11500.0), ("Mão de obra", "Eletricista", "h", 180, 30.0),
        ("Mão de obra", "Ajudante", "h", 180, 22.5)]),
    ("C-LIMP", "Limpeza final de obra", "m²", [
        ("Mão de obra", "Servente", "h", 0.3, 22.5), ("Material", "Material de limpeza", "m²", 1, 1.8)]),
]


def exemplo(projeto=None, atual: dict | None = None) -> bytes:
    """Orçamento de exemplo ligado ao cronograma de exemplo (Rubi – Obra teste)."""
    itens = [["1.1", "Serviços preliminares", None, None, None, None, None, None, None],
             ["1.1.1", "Canteiro de obras", "vb", 1, "C-CANT", None, "1.1.1", None, None],
             ["1.1.2", "Locação da obra", "m²", 420, "C-LOC", None, "1.1.2", None, None],
             ["1.2", "Fundações", None, None, None, None, None, None, None],
             ["1.2.1", "Estacas hélice contínua D40", "m", 640, "C-EST", None, "1.2.1", None, None],
             ["1.2.2", "Blocos e baldrames", "m³", 38, "C-BLO", None, "1.2.2", None, None],
             ["1.3", "Estrutura", None, None, None, None, None, None, None]]
    itens += [[f"1.3.{n}", f"Estrutura de concreto armado - Pav. {n}", "m³", 62, "C-ESTR", None, f"1.3.{n}", None, None] for n in range(1, 6)]
    itens += [["1.4", "Alvenaria", None, None, None, None, None, None, None]]
    itens += [[f"1.4.{n}", f"Alvenaria de vedação - Pav. {n}", "m²", 380, "C-ALV", None, f"1.4.{n}", None, None] for n in range(1, 6)]
    itens += [["1.5", "Instalações", None, None, None, None, None, None, None]]
    for n in range(1, 6):
        itens += [[f"1.5.{n}.1", f"Instalações hidrossanitárias - Pav. {n}", "pav", 1, "C-HID", None, f"1.5.{n}", None, None],
                  [f"1.5.{n}.2", f"Instalações elétricas - Pav. {n}", "pav", 1, "C-ELE", None, f"1.5.{n}", None, None]]
    itens += [["1.6", "Entrega", None, None, None, None, None, None, None],
              ["1.6.1", "Limpeza final", "m²", 2100, "C-LIMP", None, "1.6", None, None]]
    comps = []
    for cod, desc, un, ins in COMP_EXEMPLO:
        for k, (tipo, nome, uni, coef, preco) in enumerate(ins):
            comps.append([cod if k == 0 else None, desc if k == 0 else None, un if k == 0 else None, tipo, nome, uni, coef, preco])

    # realizado de exemplo: proporcional ao % físico de cada tarefa, com desvios típicos
    real = []
    if projeto is not None:
        dados = ler_custos("exemplo.xlsx", montar_planilha(itens, comps, [], 0.25, instrucoes=False))
        an = analisar(dados, [], projeto, atual or projeto.medicao_arquivo, dt.date.today())
        fator = {"1.1": 1.04, "1.2": 0.97, "1.3": 1.09, "1.4": 1.02, "1.5": 1.12}
        fornec = {"1.1": "Equipe própria", "1.2": "Fundações Solo Firme", "1.3": "Empreiteira Beta",
                  "1.4": "Empreiteira Alfa", "1.5": "Instaladora Gama"}
        for it in an["itens"]:
            if not it["fisico"] or not it["termino"]:
                continue
            et = ".".join(it["codigo"].split(".")[:2])
            v = it["direto"] * it["fisico"] / 100 * fator.get(et, 1.0)
            dia = min(dt.date.fromisoformat(it["termino"]), dt.date.today())
            mat = v * (it["tipos"]["mat"] / it["unit"]) if it["unit"] else 0
            if mat > 1:
                real.append([dia - dt.timedelta(days=5), it["codigo"], "Materiais - " + it["descricao"], "Fornecedores diversos", "Material", round(mat, 2)])
            if v - mat > 1:
                real.append([dia, it["codigo"], "Medição de mão de obra - " + it["descricao"], fornec.get(et, "Empreiteira"), "Serviço", round(v - mat, 2)])
    return montar_planilha(itens, comps, real, 0.25)


MESES_BR = ["jan", "fev", "mar", "abr", "mai", "jun", "jul", "ago", "set", "out", "nov", "dez"]


def rotulo_mes(m: str) -> str:
    return f"{MESES_BR[int(m[5:7]) - 1]}/{m[2:4]}"


def exportar_xlsx(a: dict) -> bytes:
    """Cronograma financeiro (etapas × meses) e orçado × realizado em Excel."""
    from openpyxl.styles import Alignment, Font, PatternFill

    wb = openpyxl.Workbook()
    azul, cinza = PatternFill("solid", fgColor="282B59"), PatternFill("solid", fgColor="E3EAE9")
    branco, neg = Font(bold=True, color="FFFFFF"), Font(bold=True)
    moeda = '#,##0.00'
    meses = [s["mes"] for s in a["serie"]]

    ws = wb.active
    ws.title = "Cronograma financeiro"
    ws.append(["Cronograma financeiro (custo direto)", None, f"Data de status {a['data_status'][8:10]}/{a['data_status'][5:7]}/{a['data_status'][:4]}"])
    ws["A1"].font = Font(bold=True, size=14)
    ws.append([])
    ws.append(["Código", "Etapa"] + [rotulo_mes(m) for m in meses] + ["Total"])
    for c in ws[3]:
        c.fill, c.font = azul, branco
        c.alignment = Alignment(horizontal="center")
    for e in a["etapas"]:
        if not e["orcado"]:
            continue
        ws.append([e["codigo"], e["nome"]] + [round(e["meses"].get(m, 0.0), 2) for m in meses] + [round(e["orcado"], 2)])
    lin = ["", "Previsto no mês"] + [round(s["previsto"], 2) for s in a["serie"]] + [round(a["totais"]["bac"], 2)]
    ws.append(lin)
    ws.append(["", "Previsto acumulado"] + [round(s["previsto_acum"], 2) for s in a["serie"]])
    ws.append(["", "Realizado no mês"] + [round(s["realizado"], 2) for s in a["serie"]] + [round(a["totais"]["cr"], 2)])
    ws.append(["", "Realizado acumulado"] + [round(s["realizado_acum"], 2) for s in a["serie"]])
    for r in range(ws.max_row - 3, ws.max_row + 1):
        for c in ws[r]:
            c.fill, c.font = cinza, neg
    for row in ws.iter_rows(min_row=4, min_col=3):
        for c in row:
            c.number_format = moeda
    ws.column_dimensions["A"].width = 9
    ws.column_dimensions["B"].width = 34
    for i in range(3, len(meses) + 4):
        ws.column_dimensions[ws.cell(3, i).column_letter].width = 14
    ws.freeze_panes = "C4"

    w2 = wb.create_sheet("Orçado x realizado")
    w2.append(["Código", "Etapa", "Orçado", "Previsto até a data", "Valor agregado", "Realizado", "Variação de custo (VA − CR)", "IDC"])
    for c in w2[1]:
        c.fill, c.font = azul, branco
        c.alignment = Alignment(wrap_text=True, vertical="center")
    for e in a["etapas"]:
        w2.append([e["codigo"], e["nome"], e["orcado"], e["vp"], e["va"], e["cr"], e["va"] - e["cr"], e["idc"]])
    t = a["totais"]
    w2.append(["", "Total", t["bac"], t["vp"], t["va"], t["cr"], t["va"] - t["cr"], t["idc"]])
    for c in w2[w2.max_row]:
        c.fill, c.font = cinza, neg
    for row in w2.iter_rows(min_row=2, min_col=3, max_col=7):
        for c in row:
            c.number_format = moeda
    for row in w2.iter_rows(min_row=2, min_col=8, max_col=8):
        for c in row:
            c.number_format = "0.00"
    for col, w in zip("ABCDEFGH", (9, 34, 15, 16, 15, 15, 18, 8)):
        w2.column_dimensions[col].width = w
    w2.row_dimensions[1].height = 30

    w3 = wb.create_sheet("Lançamentos")
    w3.append(["Data", "Código", "Etapa", "Descrição", "Fornecedor", "Tipo", "Valor", "Origem"])
    for c in w3[1]:
        c.fill, c.font = azul, branco
    for x in a["lancamentos"]:
        w3.append([dt.date.fromisoformat(x["data"]), x.get("codigo"), x.get("etapa"), x.get("descricao"), x.get("fornecedor"),
                   TIPOS.get(x.get("tipo"), "Outros"), x["valor"], x.get("origem")])
        w3.cell(w3.max_row, 1).number_format = "dd/mm/yyyy"
        w3.cell(w3.max_row, 7).number_format = moeda
    for col, w in zip("ABCDEFGH", (11, 10, 26, 44, 26, 13, 14, 10)):
        w3.column_dimensions[col].width = w
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
