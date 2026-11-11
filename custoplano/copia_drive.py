"""Cópia de segurança fora do Render: envia a cópia diária do banco ao Google Drive da empresa.

Liga sozinho quando o painel do Render tem:
  CP_DRIVE_CHAVE    conteúdo do JSON da conta de serviço do Google Cloud
  CP_DRIVE_USUARIO  conta do Workspace em nome de quem os arquivos são gravados (ex.: contato@...),
                    liberada para a conta de serviço em "Delegação em todo o domínio"
A conta de serviço só usa a permissão drive.file: enxerga apenas a pasta e os arquivos que ela
mesma criou ("Custo Plano - cópias do sistema"), nunca o resto do Drive.
"""
from __future__ import annotations

import json
import os

ESCOPO = "https://www.googleapis.com/auth/drive.file"
PASTA_NOME = "Custo Plano - cópias do sistema"
MANTER = 30  # cópias guardadas no Drive (um mês)
API = "https://www.googleapis.com/drive/v3/files"
UPLOAD = "https://www.googleapis.com/upload/drive/v3/files"
PEDACO = 8 * 1024 * 1024


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
    _limpar(s, pasta)
    return nome


def _limpar(s, pasta: str) -> None:
    q = f"'{pasta}' in parents and trashed=false and appProperties has {{ key='custoplano' and value='copia' }}"
    r = s.get(API, params={"q": q, "fields": "files(id,name)", "orderBy": "name desc", "pageSize": 200},
              timeout=60)
    r.raise_for_status()
    for f in r.json().get("files", [])[MANTER:]:
        s.delete(f"{API}/{f['id']}", timeout=60)
