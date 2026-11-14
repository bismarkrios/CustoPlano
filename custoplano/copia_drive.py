"""Cópia de segurança fora do Render: envia a cópia diária do banco ao Google Drive da empresa.

Liga sozinho quando o painel do Render tem:
  CP_DRIVE_CHAVE    conteúdo do JSON da conta de serviço do Google Cloud
  CP_DRIVE_USUARIO  conta do Workspace em nome de quem os arquivos são gravados (ex.: contato@...),
                    liberada para a conta de serviço em "Delegação em todo o domínio"
  CP_DRIVE_CRIPTO   (opcional) segredo da criptografia; com ele a cópia vai cifrada (AES-256-GCM),
                    como .db.gz.cp, e as cópias antigas sem criptografia saem do Drive. Guarde o
                    segredo também fora do Render (gerenciador de senhas): sem ele a cópia não abre.
Para abrir uma cópia cifrada:  CP_DRIVE_CRIPTO=... python copia_drive.py arquivo.db.gz.cp saida.db
A conta de serviço só usa a permissão drive.file: enxerga apenas a pasta e os arquivos que ela
mesma criou ("Custo Plano - cópias do sistema"), nunca o resto do Drive.
"""
from __future__ import annotations

import gzip
import hashlib
import json
import os
import shutil
import sys
import tempfile

ESCOPO = "https://www.googleapis.com/auth/drive.file"
PASTA_NOME = "Custo Plano - cópias do sistema"
MANTER = 30  # cópias guardadas no Drive (um mês)
API = "https://www.googleapis.com/drive/v3/files"
UPLOAD = "https://www.googleapis.com/upload/drive/v3/files"
PEDACO = 8 * 1024 * 1024


MAGICA = b"CPCRIPTO1"  # formato: MAGICA + nonce (12 bytes) + dados cifrados + etiqueta GCM (16 bytes)


def _chave(segredo: str | None = None) -> bytes | None:
    segredo = (segredo if segredo is not None else os.environ.get("CP_DRIVE_CRIPTO", "")).strip()
    return hashlib.sha256(segredo.encode()).digest() if segredo else None


def cifrar(origem: str, destino: str, segredo: str | None = None) -> None:
    """Cifra em partes (AES-256-GCM), sem carregar o arquivo inteiro na memória."""
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

    nonce = os.urandom(12)
    enc = Cipher(algorithms.AES(_chave(segredo)), modes.GCM(nonce)).encryptor()
    enc.authenticate_additional_data(MAGICA)
    with open(origem, "rb") as f, open(destino, "wb") as g:
        g.write(MAGICA + nonce)
        while (bloco := f.read(PEDACO)):
            g.write(enc.update(bloco))
        g.write(enc.finalize() + enc.tag)


def decifrar(origem: str, destino: str, segredo: str | None = None) -> None:
    """Abre uma cópia cifrada; recusa se o segredo estiver errado ou o arquivo tiver sido alterado."""
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

    tam = os.path.getsize(origem)
    with open(origem, "rb") as f:
        if f.read(len(MAGICA)) != MAGICA:
            raise ValueError("este arquivo não é uma cópia cifrada da Custo Plano")
        nonce = f.read(12)
        f.seek(tam - 16)
        tag = f.read(16)
        f.seek(len(MAGICA) + 12)
        dec = Cipher(algorithms.AES(_chave(segredo)), modes.GCM(nonce, tag)).decryptor()
        dec.authenticate_additional_data(MAGICA)
        falta = tam - 16 - len(MAGICA) - 12
        tmp = destino + ".parcial"
        with open(tmp, "wb") as g:
            while falta > 0:
                bloco = f.read(min(PEDACO, falta))
                falta -= len(bloco)
                g.write(dec.update(bloco))
            dec.finalize()  # confere a etiqueta: segredo errado ou arquivo mexido dá erro aqui
    os.replace(tmp, destino)


def testar_restauracao(cifrado: str, pasta: str) -> int:
    """Antes de enviar, abre a própria cópia cifrada num banco temporário e confere a integridade.
    Devolve o número de tabelas; erro se a cópia não abrir."""
    import sqlite3

    gz, db = os.path.join(pasta, "teste.db.gz"), os.path.join(pasta, "teste.db")
    decifrar(cifrado, gz)
    with gzip.open(gz, "rb") as f, open(db, "wb") as g:
        shutil.copyfileobj(f, g)
    con = sqlite3.connect(db)
    try:
        if con.execute("PRAGMA quick_check").fetchone()[0] != "ok":
            raise RuntimeError("a cópia restaurada não passou na conferência do banco")
        n = con.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='table'").fetchone()[0]
        tem_obras = con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='obras'").fetchone()
        obras = con.execute("SELECT COUNT(*) FROM obras").fetchone()[0] if tem_obras else 0
    finally:
        con.close()
    os.remove(gz)
    os.remove(db)
    print(f"Restauração testada: cópia cifrada abriu inteira ({n} tabelas, {obras} obras).", flush=True)
    return n


def testar_copia_do_drive() -> str:
    """Teste de restauração do mês: baixa do Drive a cópia cifrada mais recente e a abre num banco
    temporário (nunca no banco do sistema). Confere que o que está guardado fora do Render abre de verdade."""
    s = _sessao()
    pasta = _pasta(s)
    q = f"'{pasta}' in parents and trashed=false and appProperties has {{ key='custoplano' and value='copia' }}"
    r = s.get(API, params={"q": q, "fields": "files(id,name)", "orderBy": "name desc", "pageSize": 200}, timeout=60)
    r.raise_for_status()
    cifradas = [f for f in r.json().get("files", []) if f["name"].endswith(".cp")]
    if not cifradas:
        raise RuntimeError("não há cópia cifrada no Drive")
    f = cifradas[0]
    r = s.get(f"{API}/{f['id']}", params={"alt": "media"}, timeout=300)
    r.raise_for_status()
    with tempfile.TemporaryDirectory() as d:
        baixada = os.path.join(d, f["name"])
        with open(baixada, "wb") as g:
            g.write(r.content)
        testar_restauracao(baixada, d)
    return f["name"]


def listar_cifradas() -> list[dict]:
    """Cópias cifradas guardadas no Drive, da mais recente para a mais antiga: [{id, nome}]."""
    s = _sessao()
    pasta = _pasta(s)
    q = f"'{pasta}' in parents and trashed=false and appProperties has {{ key='custoplano' and value='copia' }}"
    r = s.get(API, params={"q": q, "fields": "files(id,name)", "orderBy": "name desc", "pageSize": 200}, timeout=60)
    r.raise_for_status()
    return [{"id": f["id"], "nome": f["name"]} for f in r.json().get("files", []) if f["name"].endswith(".cp")]


def baixar_e_abrir(arquivo_id: str, destino_db: str) -> str:
    """Baixa do Drive uma cópia cifrada e a deixa aberta (banco SQLite) em destino_db. Devolve o nome."""
    nomes = {f["id"]: f["nome"] for f in listar_cifradas()}
    if arquivo_id not in nomes:
        raise ValueError("essa cópia não está na pasta de cópias do Drive")
    s = _sessao()
    r = s.get(f"{API}/{arquivo_id}", params={"alt": "media"}, timeout=300)
    r.raise_for_status()
    pasta = os.path.dirname(destino_db)
    cif, gz = os.path.join(pasta, "drive.cp"), os.path.join(pasta, "drive.db.gz")
    with open(cif, "wb") as g:
        g.write(r.content)
    decifrar(cif, gz)
    with gzip.open(gz, "rb") as f, open(destino_db, "wb") as g:
        shutil.copyfileobj(f, g)
    os.remove(cif)
    os.remove(gz)
    return nomes[arquivo_id]


def configurado() -> bool:
    return bool(os.environ.get("CP_DRIVE_CHAVE", "").strip() and os.environ.get("CP_DRIVE_USUARIO", "").strip())


def _sessao():
    from google.auth.transport.requests import AuthorizedSession
    from google.oauth2 import service_account

    info = json.loads(os.environ["CP_DRIVE_CHAVE"])
    cred = service_account.Credentials.from_service_account_info(info, scopes=[ESCOPO])
    return AuthorizedSession(cred.with_subject(os.environ["CP_DRIVE_USUARIO"].strip()))


def _pasta(s) -> str:
    q = ("mimeType='application/vnd.google-apps.folder' and trashed=false and "
         "appProperties has { key='custoplano' and value='copias' }")
    r = s.get(API, params={"q": q, "fields": "files(id)", "spaces": "drive"}, timeout=60)
    r.raise_for_status()
    if r.json().get("files"):
        return r.json()["files"][0]["id"]
    r = s.post(API, params={"fields": "id"}, timeout=60, json={
        "name": PASTA_NOME, "mimeType": "application/vnd.google-apps.folder",
        "appProperties": {"custoplano": "copias"}})
    r.raise_for_status()
    return r.json()["id"]


def enviar(caminho: str) -> str:
    """Envia um arquivo à pasta de cópias no Drive (envio em partes) e apaga lá as mais antigas."""
    if _chave():
        with tempfile.TemporaryDirectory() as d:
            cifrado = os.path.join(d, os.path.basename(caminho) + ".cp")
            cifrar(caminho, cifrado)
            testar_restauracao(cifrado, d)
            return _enviar(cifrado, cifrada=True)
    return _enviar(caminho, cifrada=False)


def _enviar(caminho: str, cifrada: bool) -> str:
    s = _sessao()
    pasta = _pasta(s)
    nome, tam = os.path.basename(caminho), os.path.getsize(caminho)
    r = s.post(UPLOAD, params={"uploadType": "resumable", "fields": "id"}, timeout=60,
               headers={"X-Upload-Content-Type": "application/gzip", "X-Upload-Content-Length": str(tam)},
               json={"name": nome, "parents": [pasta], "appProperties": {"custoplano": "copia"}})
    r.raise_for_status()
    destino, enviado = r.headers["Location"], 0
    with open(caminho, "rb") as f:
        while True:
            bloco = f.read(PEDACO)
            fim = enviado + len(bloco) - 1
            r = s.put(destino, data=bloco, timeout=300,
                      headers={"Content-Range": f"bytes {enviado}-{fim}/{tam}" if bloco else f"bytes */{tam}"})
            enviado += len(bloco)
            if r.status_code in (200, 201):
                break
            if r.status_code != 308:
                r.raise_for_status()
                raise RuntimeError(f"envio interrompido (código {r.status_code})")
    _limpar(s, pasta, so_cifradas=cifrada)
    return nome


def _limpar(s, pasta: str, so_cifradas: bool = False) -> None:
    q = f"'{pasta}' in parents and trashed=false and appProperties has {{ key='custoplano' and value='copia' }}"
    r = s.get(API, params={"q": q, "fields": "files(id,name)", "orderBy": "name desc", "pageSize": 200},
              timeout=60)
    r.raise_for_status()
    arquivos = r.json().get("files", [])
    if so_cifradas:  # com a criptografia ligada, as cópias abertas (sem senha) saem do Drive
        for f in [f for f in arquivos if not f["name"].endswith(".cp")]:
            s.delete(f"{API}/{f['id']}", timeout=60)
        arquivos = [f for f in arquivos if f["name"].endswith(".cp")]
    for f in arquivos[MANTER:]:
        s.delete(f"{API}/{f['id']}", timeout=60)


if __name__ == "__main__":  # abrir uma cópia baixada do Drive: python copia_drive.py copia.db.gz.cp saida.db
    if len(sys.argv) != 3 or not os.environ.get("CP_DRIVE_CRIPTO"):
        sys.exit("uso: CP_DRIVE_CRIPTO=<segredo> python copia_drive.py copia.db.gz.cp saida.db")
    gz = sys.argv[2] + ".gz"
    decifrar(sys.argv[1], gz)
    with gzip.open(gz, "rb") as f, open(sys.argv[2], "wb") as g:
        shutil.copyfileobj(f, g)
    os.remove(gz)
    print(f"Cópia aberta: {sys.argv[2]}")
