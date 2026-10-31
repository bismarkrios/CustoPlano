"""Resumo de engenharia de cada obra de cliente nos 3 pilares: prazo (PPC, medição), custo (orçado x realizado,
IDC/IDP) e qualidade (NCs, FVS atrasadas e indicadores), com os alertas que pedem ação, escrito uma vez por dia no log do servidor. A conferência semanal da equipe lê essa linha nos logs do Render, sem precisar de login.
Só números e o nome da obra: nenhum e-mail, senha ou dado pessoal vai para o log."""
import datetime as dt
import json

import banco
import qualidade

MARCA = "RESUMO-ENGENHARIA"
DIAS_MEDICAO = 7  # data de status mais antiga que isso = medição atrasada
DIAS_QUALIDADE = 30  # janela dos indicadores de qualidade (inspeções e NCs fechadas)


def _ppc(semanas: dict, hoje: dt.date) -> dict | None:
    """PPC da última semana já começada que tem apontamento: concluídos / apontados."""
    for sem in sorted(semanas, reverse=True):
        if sem > hoje.isoformat():
            continue
        ap = semanas[sem].values()
        ok = sum(1 for a in ap if a.get("st") == "ok")
        nok = sum(1 for a in ap if a.get("st") == "nok")
        if ok + nok:
            causas: dict[str, int] = {}
            for a in ap:
                if a.get("st") == "nok" and a.get("causa"):
                    causas[a["causa"]] = causas.get(a["causa"], 0) + 1
            return {"semana": sem, "ppc": round(100 * ok / (ok + nok), 2), "ok": ok, "nok": nok, "causas": causas}
    return None


def _qualidade(con, uid: int, hoje: dt.date) -> dict:
    """Indicadores de qualidade dos últimos 30 dias: inspeções, aprovação na 1ª inspeção, NCs por serviço,
    prazo médio de fechamento e NCs abertas sem responsável."""
    desde = (hoje - dt.timedelta(days=DIAS_QUALIDADE)).isoformat()
    insp = con.execute("SELECT servico, resultado, reinspecao_de FROM inspecoes WHERE usuario_id=? AND data>=? AND data<=?",
                       (uid, desde, hoje.isoformat())).fetchall()
    primeiras = [i for i in insp if not i["reinspecao_de"]]
    aprov = sum(1 for i in primeiras if i["resultado"] == "aprovada")
    ncs = con.execute("SELECT servico, status, responsavel, criado_em, fechada_em FROM ncs WHERE usuario_id=?", (uid,)).fetchall()
    abertas = [n for n in ncs if n["status"] != "fechada"]
    por_servico: dict[str, int] = {}
    for n in abertas:
        chave = n["servico"] or "Sem serviço"
        por_servico[chave] = por_servico.get(chave, 0) + 1
    dias = [(dt.date.fromisoformat(n["fechada_em"][:10]) - dt.date.fromisoformat(n["criado_em"][:10])).days
            for n in ncs if n["status"] == "fechada" and n["fechada_em"] and n["fechada_em"][:10] >= desde]
    return {"inspecoes_30d": len(insp), "reinspecoes_30d": len(insp) - len(primeiras),
            "aprovacao_1a_pct": round(100 * aprov / len(primeiras), 2) if primeiras else None,
            "servicos_inspecionados": sorted({i["servico"] for i in insp}),
            "ncs_abertas_por_servico": por_servico, "ncs_sem_responsavel": sum(1 for n in abertas if not n["responsavel"]),
            "ncs_fechadas_30d": len(dias), "dias_medio_fechamento": round(sum(dias) / len(dias), 1) if dias else None}


def _custo(analise: dict | None) -> dict:
    """Só índices e percentuais da análise de custos (a mesma da tela Custos): nenhum valor em reais vai para o log."""
    if not analise:
        return {"orcamento": "não importado"}
    C = (analise.get("controle") or {}).get("total")
    if C:  # a planilha Orçado x Realizado é a informação de custo da obra
        pc = lambda v: round(100 * v / C["oatu"], 1) if C["oatu"] else None  # noqa: E731
        return {"fonte": "orcado x realizado", "indice": C["indice"], "contratado_pct": pc(C["contratado"]),
                "incorrido_pct": pc(C["incorrido"]), "a_contratar_pct": pc(C["a_contratar"]), "desvio_final_pct": pc(C["desvio"])}
    t = analise["totais"]
    if analise.get("sem_realizado"):  # sem lançamento real não há realizado nem IDC: nada é estimado
        pct = lambda v: round(100 * v / t["bac"], 1) if v is not None and t.get("bac") else None  # noqa: E731
        r2 = lambda v: round(v, 2) if v is not None else None  # noqa: E731
        return {"realizado": "sem lançamentos reais", "idp": r2(t["idp"]), "previsto_ate_hoje_pct": pct(t["vp"]),
                "agregado_pct": pct(t["va"])}
    r2 = lambda v: round(v, 2) if v is not None else None  # noqa: E731
    pct = lambda v: round(100 * v / t["bac"], 1) if v is not None and t.get("bac") else None  # noqa: E731
    return {"idc": r2(t["idc"]), "idp": r2(t["idp"]), "previsto_ate_hoje_pct": pct(t["vp"]), "agregado_pct": pct(t["va"]),
            "realizado_pct": pct(t["cr"]), "desvio_final_pct": pct(t["vac"])}


def _alertas(r: dict) -> list[str]:
    """O que pede ação da equipe nesta obra, em frases curtas (só números, sem dado pessoal)."""
    a = []
    if r.get("medicao_atrasada_dias"):
        a.append(f"medição física parada há {r['medicao_atrasada_dias']} dias (data de status {r['data_status']}"
                 + (f", última medição fechada em {r['ultima_medicao']['data']})" if r.get("ultima_medicao") else ")"))
    if isinstance(r.get("fvs_atrasadas"), int) and r["fvs_atrasadas"]:
        a.append(f"{r['fvs_atrasadas']} FVS do cronograma com a inspeção vencida")
    if r.get("ncs_vencidas"):
        a.append(f"{r['ncs_vencidas']} NCs com o prazo vencido")
    return a


def resumo_obras(hoje: dt.date | None = None, analise_custos=None, projeto_da_obra=None) -> list[dict]:
    """analise_custos(obra_id) -> análise de custos da obra (app.analise_custos); sem ela, o custo fica de fora.
    projeto_da_obra(obra_id) -> cronograma da obra (Projeto), para contar as FVS atrasadas; sem ele, elas ficam de fora."""
    hoje = hoje or dt.date.today()
    with banco.conectar() as con:
        obras = con.execute(
            "SELECT id, nome, empresa FROM obras WHERE id NOT IN ("
            " SELECT a.obra_id FROM acessos a JOIN usuarios u ON u.id = a.usuario_id"
            " WHERE u.email = 'demo@custoplano.com.br') ORDER BY id").fetchall()
        saida = []
        for o in obras:
            r = {"obra": o["nome"], "empresa": o["empresa"]}
            c = con.execute("SELECT data_status FROM cronogramas WHERE usuario_id=?", (o["id"],)).fetchone()
            if c:
                atraso = (hoje - dt.date.fromisoformat(c["data_status"][:10])).days
                r["data_status"] = c["data_status"][:10]
                r["medicao_atrasada_dias"] = atraso if atraso > DIAS_MEDICAO else 0
            else:
                r["cronograma"] = "não importado"
            m = con.execute("SELECT data_status, avanco_real, avanco_prev FROM medicoes_fechadas WHERE usuario_id=? "
                            "ORDER BY id DESC LIMIT 1", (o["id"],)).fetchone()
            if m:
                r["ultima_medicao"] = {"data": m["data_status"][:10], "real": round(m["avanco_real"], 1),
                                       "previsto": round(m["avanco_prev"], 1)}
                # a data de status pode andar sem medição nova: o atraso conta da última medição fechada
                parada = (hoje - dt.date.fromisoformat(m["data_status"][:10])).days
                if parada > DIAS_MEDICAO and parada > (r.get("medicao_atrasada_dias") or 0):
                    r["medicao_atrasada_dias"] = parada
            cp = con.execute("SELECT dados FROM curto_prazo WHERE usuario_id=?", (o["id"],)).fetchone()
            r["ppc"] = _ppc(json.loads(cp["dados"]) if cp else {}, hoje)
            ncs = con.execute("SELECT status, prazo, gravidade FROM ncs WHERE usuario_id=? AND status != 'fechada'",
                              (o["id"],)).fetchall()
            r["ncs_abertas"] = len(ncs)
            r["ncs_vencidas"] = sum(1 for n in ncs if n["prazo"] and n["prazo"][:10] < hoje.isoformat())
            r["ncs_altas"] = sum(1 for n in ncs if n["gravidade"] == "alta")
            r["qualidade"] = _qualidade(con, o["id"], hoje)
            if projeto_da_obra and c:
                try:
                    r["fvs_atrasadas"] = qualidade.fvs_atrasadas(o["id"], projeto_da_obra(o["id"]), hoje)
                except Exception as e:  # noqa: BLE001 - um cronograma com problema não tira a obra do resumo
                    r["fvs_atrasadas"] = {"erro": str(e)[:120]}
            if analise_custos:
                try:
                    r["custo"] = _custo(analise_custos(o["id"]))
                except Exception as e:  # noqa: BLE001 - um orçamento com problema não tira a obra do resumo
                    r["custo"] = {"erro": str(e)[:120]}
            r["alertas"] = _alertas(r)
            saida.append(r)
    return saida


def indicadores_obra(obra_id: int, hoje: dt.date | None = None) -> dict:
    """PPC, NCs e qualidade de uma obra, para o relatório mensal (os mesmos números do resumo diário)."""
    hoje = hoje or dt.date.today()
    with banco.conectar() as con:
        cp = con.execute("SELECT dados FROM curto_prazo WHERE usuario_id=?", (obra_id,)).fetchone()
        ncs = con.execute("SELECT status, prazo, gravidade FROM ncs WHERE usuario_id=? AND status != 'fechada'",
                          (obra_id,)).fetchall()
        return {"ppc": _ppc(json.loads(cp["dados"]) if cp else {}, hoje), "ncs_abertas": len(ncs),
                "ncs_vencidas": sum(1 for n in ncs if n["prazo"] and n["prazo"][:10] < hoje.isoformat()),
                "ncs_altas": sum(1 for n in ncs if n["gravidade"] == "alta"), "qualidade": _qualidade(con, obra_id, hoje)}


def registrar_no_log(hoje: dt.date | None = None, analise_custos=None, projeto_da_obra=None) -> None:
    hoje = hoje or dt.date.today()
    obras = resumo_obras(hoje, analise_custos, projeto_da_obra)
    print(f"{MARCA} {json.dumps({'data': hoje.isoformat(), 'obras': obras}, ensure_ascii=False)}", flush=True)
