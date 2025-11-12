#!/usr/bin/env python3
"""
Seed Issues from Excel (idempotente)
- Lê backlog/ISSUE.xlsx (ou --file <caminho>)
- Respeita --dry-run (não cria, só relata)
- Cria labels faltantes (Semana, SQUAD, Papel, IA, IdAluno)
- Evita duplicatas procurando seed_id no corpo ou pela label "seed:<uid>"
- Gera seed-report.csv com o resultado de cada linha
Requisitos: pandas, openpyxl, requests
"""

import argparse, os, sys, time, csv, re
import pandas as pd
import requests

GH_API = "https://api.github.com"

def gh(headers, method, path, **kwargs):
    url = f"{GH_API}{path}"
    r = requests.request(method, url, headers=headers, timeout=30, **kwargs)
    # rate limit básico
    if r.status_code == 403 and "rate limit" in r.text.lower():
        time.sleep(5)
        r = requests.request(method, url, headers=headers, timeout=30, **kwargs)
    r.raise_for_status()
    return r

def ensure_label(headers, repo, name, color="5865F2", desc=None):
    try:
        gh(headers, "GET", f"/repos/{repo}/labels/{name}")
    except requests.HTTPError as e:
        if e.response.status_code == 404:
            payload = {"name": name, "color": color}
            if desc: payload["description"] = desc
            gh(headers, "POST", f"/repos/{repo}/labels", json=payload)
        else:
            raise

def find_issue_by_seed(headers, repo, seed_uid):
    """
    Evita a Search API (que pode dar 403 no GITHUB_TOKEN).
    Procura a issue pela label 'seed:<uid>' usando a listagem normal.
    """
    label = f"seed:{seed_uid}"
    page = 1
    per_page = 50
    while True:
        r = gh(
            headers, "GET",
            f"/repos/{repo}/issues",
            params={"state": "all", "labels": label, "per_page": per_page, "page": page}
        )
        items = r.json()
        if not items:
            return None
        for it in items:
            # garante que é issue (não PR) e que tem a label exata
            if "pull_request" not in it:
                labels = {l["name"] for l in it.get("labels", [])}
                if label in labels:
                    return it
        if len(items) < per_page:
            return None
        page += 1

def build_body(row):
    parts = []
    def add(h, v):
        v = "" if pd.isna(v) else str(v)
        if v.strip():
            parts.append(f"**{h}:** {v}")
    add("Semana", row.get("Semana"))
    add("SQUAD", row.get("SQUAD"))
    add("Papel", row.get("Papel"))
    add("Id Aluno", row.get("Id Aluno"))
    add("IA", row.get("IA"))
    add("Tarefa", row.get("Tarefa"))
    add("Descrição", row.get("Descrição"))
    add("Entregáveis", row.get("Entregáveis"))
    add("Critérios de Aceite", row.get("Critérios de Aceite"))
    add("Arquivos Sugeridos", row.get("Arquivos Sugeridos"))
    add("Comando de Verificação", row.get("Comando de Verificação"))
    add("Branch Sugerida", row.get("Branch Sugerida"))
    add("Revisor", row.get("Revisor"))
    add("Observações", row.get("Observações"))
    seed_uid = str(row.get("Issue UID", "")).strip()
    parts.append(f"\n<!-- seed_id:{seed_uid} -->")
    return "\n".join(parts)

def main():
    p = argparse.ArgumentParser()
    p.add_argument("--file", default="backlog/ISSUE.xlsx")
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()

    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    repo = os.environ.get("GITHUB_REPOSITORY")
    if not token or not repo:
        print("Faltam variáveis: GITHUB_TOKEN e/ou GITHUB_REPOSITORY", file=sys.stderr)
        sys.exit(2)

    headers = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"}

    # Ler Excel (aceita múltiplas abas, concatena)
    xl = pd.ExcelFile(args.file)
    frames = []
    for s in xl.sheet_names:
        df = xl.parse(s)
        if len(df.columns) == 0: 
            continue
        df["__sheet__"] = s
        frames.append(df)
    if not frames:
        print("Excel sem conteúdo lido.", file=sys.stderr)
        sys.exit(3)
    df = pd.concat(frames, ignore_index=True)

    # Normalizar colunas esperadas
    for c in ["Semana","SQUAD","Papel","Tarefa","IA","Id Aluno","Issue UID","Título do PR"]:
        if c not in df.columns:
            df[c] = ""

    # Garantir labels-base
    base_labels = [
        ("Tipo:feature","3BA55D","Tarefas de implementação"),
        ("Tipo:bug","ED4245","Correções"),
    ]
    for name, color, desc in base_labels:
        ensure_label(headers, repo, name, color, desc)

    # Rodar linhas
    report_rows = []
    for idx, row in df.fillna("").iterrows():
        seed_uid = str(row.get("Issue UID") or "").strip()
        titulo = str(row.get("Tarefa") or "").strip()
        if not seed_uid or not titulo:
            report_rows.append(("skip", idx+1, titulo, "", "missing uid/title", ""))
            continue

        # Labels dinâmicas
        labels = []
        semana = str(row.get("Semana")).strip()
        squad  = str(row.get("SQUAD")).strip()
        papel  = str(row.get("Papel")).strip()
        ia     = str(row.get("IA")).strip()
        aluno  = str(row.get("Id Aluno")).strip()

        dyn = [
            (f"Semana:{semana}", "7289DA"),
            (f"SQUAD:{squad}", "99AAB5"),
            (f"Papel:{papel}", "FEE75C"),
            (f"IA:{ia}", "FAA61A"),
            (f"IdAluno:{aluno}", "57F287"),
            (f"seed:{seed_uid}", "99AAB5"),
        ]
        for name,color in dyn:
            if name.endswith(":") or name.endswith(":nan") or name.endswith(":NaN"):
                continue
            ensure_label(headers, repo, name, color)

        # Idempotência
        existing = find_issue_by_seed(headers, repo, seed_uid)
        if existing:
            report_rows.append(("exists", idx+1, titulo, existing["html_url"], f"seed:{seed_uid}", ""))
            continue

        body = build_body(row)
        payload = {"title": titulo, "body": body, "labels": [n for n,_ in dyn if not n.endswith(":")]}
        if args.dry_run:
            report_rows.append(("dry-run", idx+1, titulo, "", "", ""))
            continue

        try:
            r = gh(headers, "POST", f"/repos/{repo}/issues", json=payload)
            url = r.json().get("html_url","")
            report_rows.append(("created", idx+1, titulo, url, f"seed:{seed_uid}", ""))
            time.sleep(0.3)  # suaviza rate limit
        except Exception as e:
            report_rows.append(("error", idx+1, titulo, "", "", str(e)))

    # Salvar relatório
    with open("seed-report.csv","w", newline='', encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["status","row","task","issue","reason","error"])
        w.writerows(report_rows)

    print(f"Seed finalizado. Linhas: {len(report_rows)}. Relatório: seed-report.csv")

if __name__ == "__main__":
    main()
