#!/bin/sh
# Ao fim de cada resposta do Claude: grava as mudanças e envia para o GitHub.
# Nunca envia direto para main: se estiver em main, cria uma branch claude/...
# O GitHub Actions (publicar.yml) testa essa branch, leva para main e o Render publica.

# Acha a raiz do repositório: primeiro pela pasta do projeto, depois pela pasta deste script.
raiz=""
for pasta in "$CLAUDE_PROJECT_DIR" "$(dirname "$0")"; do
  [ -n "$pasta" ] || continue
  raiz=$(git -C "$pasta" rev-parse --show-toplevel 2>/dev/null) && break
  raiz=""
done
[ -n "$raiz" ] || exit 0
cd "$raiz" || exit 0

aviso() { printf '{"systemMessage": "%s"}\n' "$1"; }

branch=$(git rev-parse --abbrev-ref HEAD 2>/dev/null) || exit 0

# Sem branch (HEAD solto) ou em main: passa para uma branch claude/ nova.
# As mudanças e commits ainda não enviados vão junto para ela.
if [ "$branch" = "HEAD" ] || [ "$branch" = "main" ] || [ "$branch" = "master" ]; then
  tem_mudanca=$(git status --porcelain)
  a_frente=""
  if git rev-parse -q --verify "origin/$branch" >/dev/null; then
    a_frente=$(git rev-list "origin/$branch..HEAD" 2>/dev/null)
  fi
  [ -z "$tem_mudanca" ] && [ -z "$a_frente" ] && exit 0
  nova="claude/auto-$(date -u '+%Y%m%d-%H%M%S')"
  git switch -q -c "$nova" || { aviso "Não consegui criar a branch $nova."; exit 0; }
  branch="$nova"
fi

if [ -n "$(git status --porcelain)" ]; then
  git add -A
  git commit -q -m "Atualização automática ($(date -u '+%Y-%m-%d %H:%M') UTC)" \
    -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>" || exit 0
fi

# Nada a enviar?
if git rev-parse -q --verify "origin/$branch" >/dev/null &&
   [ -z "$(git rev-list "origin/$branch..HEAD")" ]; then
  exit 0
fi

erro=""
for espera in 2 4 8 16 0; do
  if erro=$(git push -q -u origin "$branch" 2>&1); then
    aviso "Mudanças enviadas ao GitHub (branch $branch). Os testes rodam e, se passarem, vão para main."
    exit 0
  fi
  [ "$espera" -gt 0 ] && sleep "$espera"
done
motivo=$(printf '%s' "$erro" | tail -n 1 | tr -d '"\\' | cut -c1-200)
aviso "Não consegui enviar ao GitHub (branch $branch): $motivo"
