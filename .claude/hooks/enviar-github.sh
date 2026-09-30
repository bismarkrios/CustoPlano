#!/bin/sh
# Ao fim de cada resposta do Claude: grava as mudanças e envia a branch para o GitHub.
# O GitHub Actions (publicar.yml) testa, leva para main e o Render publica.
cd "$CLAUDE_PROJECT_DIR" || exit 0
branch=$(git rev-parse --abbrev-ref HEAD) || exit 0
[ "$branch" = "HEAD" ] && exit 0

if [ -n "$(git status --porcelain)" ]; then
  git add -A
  git commit -q -m "Atualização automática ($(date -u '+%Y-%m-%d %H:%M') UTC)" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>" || exit 0
fi

# Nada a enviar?
if git rev-parse -q --verify "origin/$branch" >/dev/null &&
   [ -z "$(git rev-list "origin/$branch..HEAD")" ]; then
  exit 0
fi

for espera in 2 4 8 16 0; do
  if git push -q -u origin "$branch" 2>/dev/null; then
    echo "{\"systemMessage\": \"Mudanças enviadas ao GitHub (branch $branch).\"}"
    exit 0
  fi
  [ "$espera" -gt 0 ] && sleep "$espera"
done
echo "{\"systemMessage\": \"Não consegui enviar ao GitHub (branch $branch).\"}"
