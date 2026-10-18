"""Resumo de engenharia de cada obra de cliente nos 3 pilares: prazo (PPC, medição), custo (orçado x realizado,
IDC/IDP) e qualidade (NCs e indicadores), escrito uma vez por dia no log do servidor. A conferência semanal da equipe lê essa linha nos logs do Render, sem precisar de login.
Só números e o nome da obra: nenhum e-mail, senha ou dado pessoal vai para o log."""
import datetime as dt
import json

import banco

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
            return {"semana": sem, "ppc": round(100 * ok / (ok + nok)), "ok": ok, "nok": nok, "causas": causas}
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
            "aprovacao_1a_pct": round(100 * aprov / len(primeiras)) if primeiras else None,
            "servicos_inspecionados": sorted({i["servico"] for i in insp}),
            "ncs_abertas_por_servico": por_servico, "ncs_sem_responsavel": sum(1 for n in abertas if not n["responsavel"]),
            "ncs_fechadas_30d": len(dias), "dias_medio_fechamento": round(sum(dias) / len(dias), 1) if dias else None}


def _custo(analise: dict | None) -> dict:
    """Só os totais da análise de custos (a mesma da tela Custos)."""
    if not analise:
        return {"orcamento": "não importado"}
    t = analise["totais"]
    r2 = lambda v: round(v, 2) if v is not None else None  # noqa: E731
    return {"orcado": r2(t["bac"]), "previsto_ate_hoje": r2(t["vp"]), "agregado": r2(t["va"]), "realizado": r2(t["cr"]),
            "idc": r2(t["idc"]), "idp": r2(t["idp"]), "estimativa_final": r2(t["eac"]), "desvio_final": r2(t["vac"])}


def resumo_obras(hoje: dt.date | None = None, analise_custos=None) -> list[dict]:
    """analise_custos(obra_id) -> análise de custos da obra (app.analise_custos); sem ela, o custo fica de fora."""
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
            cp = con.execute("SELECT dados FROM curto_prazo WHERE usuario_id=?", (o["id"],)).fetchone()
            r["ppc"] = _ppc(json.loads(cp["dados"]) if cp else {}, hoje)
            ncs = con.execute("SELECT status, prazo, gravidade FROM ncs WHERE usuario_id=? AND status != 'fechada'",
                              (o["id"],)).fetchall()
            r["ncs_abertas"] = len(ncs)
            r["ncs_vencidas"] = sum(1 for n in ncs if n["prazo"] and n["prazo"][:10] < hoje.isoformat())
            r["ncs_altas"] = sum(1 for n in ncs if n["gravidade"] == "alta")
            r["qualidade"] = _qualidade(con, o["id"], hoje)
            if analise_custos:
                try:
                    r["custo"] = _custo(analise_custos(o["id"]))
                except Exception as e:  # noqa: BLE001 - um orçamento com problema não tira a obra do resumo
                    r["custo"] = {"erro": str(e)[:120]}
            saida.append(r)
    return saida


def registrar_no_log(hoje: dt.date | None = None, analise_custos=None) -> None:
    hoje = hoje or dt.date.today()
    obras = resumo_obras(hoje, analise_custos)
    print(f"{MARCA} {json.dumps({'data': hoje.isoformat(), 'obras': obras}, ensure_ascii=False)}", flush=True)
