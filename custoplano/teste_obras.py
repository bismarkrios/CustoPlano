"""Teste das obras e dos acessos: várias pessoas na mesma obra, acesso só de consulta,
administração só para a equipe e links de senha de uso único.
Rodar com:  CP_BANCO=/tmp/obras.db CP_SECRET=x CP_COPIAS_AUTO=0 python teste_obras.py
"""
import io
import os
import sys

import app
import banco

falhas = []


def checar(cond, msg):
    if not cond:
        falhas.append(msg)


def entrar(email, senha="senha-forte-123"):
    c = app.app.test_client()
    r = c.post("/api/login", json={"email": email, "senha": senha})
    assert r.status_code == 200, (email, r.get_json())
    return c


# ---- equipe Custo Plano cria duas obras e três pessoas
banco.criar_usuario("equipe@teste.com", "senha-forte-123", "Equipe", admin=True)
adm = entrar("equipe@teste.com")
o1 = adm.post("/api/admin/obra", json={"nome": "Torre Norte", "empresa": "Construtora X"}).get_json()["id"]
o2 = adm.post("/api/admin/obra", json={"nome": "Casa Sul", "empresa": "Construtora Y"}).get_json()["id"]
r = adm.post("/api/admin/usuario", json={"email": "Eng@X.com", "nome": "Engenheiro", "acessos": [{"obra_id": o1, "papel": "editor"}]})
checar(r.status_code == 200 and "#senha=" in r.get_json()["link"], "não criou o usuário com link")
link_eng = r.get_json()["link"].split("#senha=")[1]
r = adm.post("/api/admin/usuario", json={"email": "dir@x.com", "acessos": [{"obra_id": o1, "papel": "leitor"}]})
link_dir = r.get_json()["link"].split("#senha=")[1]
checar(adm.post("/api/admin/usuario", json={"email": "eng@x.com"}).status_code == 400, "deixou criar e-mail repetido")
checar(adm.post("/api/admin/usuario", json={"email": "sem-arroba"}).status_code == 400, "aceitou e-mail inválido")

# ---- sem senha criada, ninguém entra; o link cria a senha e já abre a obra
anon = app.app.test_client()
checar(anon.post("/api/login", json={"email": "eng@x.com", "senha": ""}).status_code == 401, "entrou sem senha")
checar(anon.post("/api/senha/link", json={"codigo": "errado"}).status_code == 400, "aceitou link inventado")
checar(anon.post("/api/senha/link", json={"codigo": link_eng}).get_json().get("email") == "eng@x.com", "link válido recusado")
checar(anon.post("/api/senha/definir", json={"codigo": link_eng, "senha": "curta"}).status_code == 400, "aceitou senha curta")
eng = app.app.test_client()
r = eng.post("/api/senha/definir", json={"codigo": link_eng, "senha": "senha-do-eng-1"})
checar(r.status_code == 200 and r.get_json()["obra"]["id"] == o1, "link não abriu a obra do engenheiro")
checar(anon.post("/api/senha/definir", json={"codigo": link_eng, "senha": "outra-senha-9"}).status_code == 400, "link usado duas vezes")
dire = app.app.test_client()
dire.post("/api/senha/definir", json={"codigo": link_dir, "senha": "senha-do-dir-1"})

# ---- engenheiro (editor) importa; diretor (leitor) vê o mesmo cronograma mas não altera nada
xml = open(os.path.join(app.AQUI, "exemplo", "cronograma_exemplo.xml"), "rb").read()
r = eng.post("/api/importar", data={"arquivo": (io.BytesIO(xml), "torre_norte.xml")}, content_type="multipart/form-data")
checar(r.status_code == 200, "editor não conseguiu importar")
checar(dire.get("/api/cronograma").get_json()["xml"] is not None, "leitor não vê o cronograma da obra")
checar(dire.post("/api/custos/exemplo").status_code == 403, "leitor conseguiu gravar custos")
checar(dire.post("/api/remover").status_code == 403, "leitor conseguiu apagar o cronograma")
checar(dire.post("/api/curtoprazo", json={}).status_code == 403, "leitor conseguiu gravar o curto prazo")
checar(eng.get("/api/cronograma").get_json()["xml"] is not None, "o cronograma sumiu")

# ---- gerentes: cada um consulta tudo e altera só a sua parte
gerentes = {}
for email, p in (("plan@x.com", "planejamento"), ("orc@x.com", "orcamento")):
    r = adm.post("/api/admin/usuario", json={"email": email, "acessos": [{"obra_id": o1, "papel": p}]})
    c = app.app.test_client()
    c.post("/api/senha/definir", json={"codigo": r.get_json()["link"].split("#senha=")[1], "senha": "senha-gerente-1"})
    checar(c.get("/api/me").get_json()["obra"]["papel"] == p, f"papel {p} não ficou gravado")
    gerentes[p] = c
plan, orc = gerentes["planejamento"], gerentes["orcamento"]
checar(plan.get("/api/custos").status_code == 200, "gerente de planejamento não consulta custos")
checar(plan.post("/api/custos/exemplo").status_code == 403, "gerente de planejamento alterou custos")
checar(plan.post("/api/curtoprazo", json={}).status_code != 403, "gerente de planejamento não grava o curto prazo")
checar(plan.post("/api/suprimentos", json={}).status_code != 403, "gerente de planejamento não grava suprimentos")
checar(orc.get("/api/cronograma").get_json()["xml"] is not None, "gerente de orçamento não consulta o cronograma")
checar(orc.post("/api/custos/exemplo").status_code != 403, "gerente de orçamento não grava custos")
checar(orc.post("/api/remover").status_code == 403, "gerente de orçamento apagou o cronograma")
checar(orc.post("/api/curtoprazo", json={}).status_code == 403, "gerente de orçamento alterou o curto prazo")
checar(orc.post("/api/suprimentos", json={}).status_code != 403, "gerente de orçamento não grava suprimentos")
checar(orc.post("/api/qualidade/nc", json={}).status_code == 403, "gerente de orçamento alterou a qualidade")
checar(adm.post("/api/admin/acesso", json={"usuario_id": 1, "obra_id": o1, "papel": "chefe"}).status_code == 400, "aceitou papel inventado")

# ---- ninguém abre obra que não é sua nem entra na administração
checar(eng.post("/api/obra/abrir", json={"id": o2}).status_code == 403, "engenheiro abriu obra de outra empresa")
checar(eng.get("/api/admin").status_code == 403, "cliente entrou na administração")
checar(eng.post("/api/admin/obra", json={"nome": "invasão"}).status_code == 403, "cliente criou obra")
checar(eng.post("/api/admin/link-senha", json={"usuario_id": 1}).status_code == 403, "cliente gerou link de senha")
checar(eng.get("/api/admin/copia").status_code == 403, "cliente baixou a cópia do banco")
checar(anon.get("/api/admin").status_code == 401, "visitante entrou na administração")

# ---- outra obra do mesmo engenheiro: troca e os dados não se misturam
adm.post("/api/admin/acesso", json={"usuario_id": banco.usuario_por_email("eng@x.com")["id"], "obra_id": o2, "papel": "editor"})
checar(len(eng.get("/api/me").get_json()["obras"]) == 2, "engenheiro não vê as duas obras")
checar(eng.post("/api/obra/abrir", json={"id": o2}).status_code == 200, "não trocou de obra")
checar(eng.get("/api/cronograma").get_json()["xml"] is None, "a obra nova mostrou o cronograma da outra")
eng.post("/api/obra/abrir", json={"id": o1})

# ---- tirar o acesso derruba na hora
adm.post("/api/admin/acesso", json={"usuario_id": banco.usuario_por_email("dir@x.com")["id"], "obra_id": o1, "papel": None})
checar(dire.get("/api/cronograma").status_code == 409, "leitor continuou vendo a obra depois de perder o acesso")

# ---- link novo invalida o anterior; trocar a senha derruba as outras sessões
uid_eng = banco.usuario_por_email("eng@x.com")["id"]
l1 = adm.post("/api/admin/link-senha", json={"usuario_id": uid_eng}).get_json()["link"].split("#senha=")[1]
l2 = adm.post("/api/admin/link-senha", json={"usuario_id": uid_eng}).get_json()["link"].split("#senha=")[1]
checar(anon.post("/api/senha/link", json={"codigo": l1}).status_code == 400, "link antigo continuou valendo")
outra = app.app.test_client()
outra.post("/api/senha/definir", json={"codigo": l2, "senha": "senha-nova-eng"})
checar(eng.get("/api/cronograma").status_code == 401, "sessão antiga continuou depois da troca de senha")

# ---- apagar obra pede o nome e leva os dados junto; não dá para remover a equipe
checar(adm.post("/api/admin/obra/remover", json={"id": o1, "confirmar": "errado"}).status_code == 400, "apagou sem confirmar o nome")
checar(adm.post("/api/admin/usuario/remover", json={"id": banco.usuario_por_email("equipe@teste.com")["id"]}).status_code == 403,
       "removeu a própria conta da equipe")
checar(adm.post("/api/admin/obra/remover", json={"id": o1, "confirmar": "torre norte"}).status_code == 200, "não apagou a obra")
checar(banco.cronograma(o1) is None, "os dados da obra apagada ficaram no banco")
checar(any("antes-de-apagar" in c["nome"] for c in banco.listar_copias()), "não guardou cópia antes de apagar a obra")

# ---- cópia baixada abre e tem as tabelas
r = adm.get("/api/admin/copia")
checar(r.status_code == 200 and r.data[:2] == b"\x1f\x8b", "a cópia baixada não é um .gz")
checar(app.app.test_client().get("/saude").status_code == 200, "/saude não respondeu")

if falhas:
    print("FALHOU:\n- " + "\n- ".join(falhas))
    sys.exit(1)
print("obras e acessos OK")
