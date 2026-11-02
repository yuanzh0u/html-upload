#!/usr/bin/env bash
umask 077
mkdir -p -- "$HOME/.config/docs"
chmod 700 -- "$HOME/.config/docs"
tmp=$(mktemp --tmpdir="$HOME/.config/docs" .api-key.XXXXXXXX)
chmod 600 -- "$tmp"
IFS= read -rs KEY || exit 1
printf '%s' "$KEY" > "$tmp"
unset KEY
mv -f -- "$tmp" "$HOME/.config/docs/api-key"
chmod 600 -- "$HOME/.config/docs/api-key"
