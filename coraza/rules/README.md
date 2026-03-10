# Coraza/OWASP CRS-Regeln

Das OWASP Coreruleset (CRS) ist als **Git-Submodule** unter `coraza/rules/coreruleset/` eingebunden. Lade-Reihenfolge in `conf/coraza-spoa.yaml`: zuerst CRS, danach `custom/*.conf`.

## Customizing: Nur `custom/` anpassen

**CRS-Dateien im Submodule nicht bearbeiten.** Alle Anpassungen (Regeln deaktivieren, eigene Regeln, Tuning) gehören in **`coraza/rules/custom/`**. Die Dateien dort werden nach dem CRS geladen; so bleiben eure Änderungen erhalten und das CRS kann jederzeit aktualisiert werden. Siehe `custom/README.md`.

## Erstes Klonen / Nach dem Klonen

Submodule einmal initialisieren:

```bash
git submodule update --init --recursive
```

Oder beim Klonen: `git clone --recurse-submodules <repo-url>`

## CRS von Zeit zu Zeit aktualisieren?

**Empfehlung: ja.** Upstream liefert Bugfixes und neue Signaturen; für ein produktives System lohnt sich ein Update alle paar Monate oder bei Hinweisen auf CVEs.

Neue CRS-Version einspielen (Submodule auf anderen Tag setzen):

```bash
cd coraza/rules/coreruleset
git fetch --tags
git checkout v4.xx.x   # gewünschten Tag, z. B. v4.24.1
cd ../../..
git add coraza/rules/coreruleset
git commit -m "chore: CRS auf v4.xx.x aktualisieren"
```

Eure Anpassungen in `custom/` bleiben unberührt. Nach dem Update ggf. kurz testen (False Positives, erwartetes Blocking).

## Inhalt CRS-Submodule

- `crs-setup.conf.example` – CRS-Basis-Konfiguration
- `rules/*.conf` – Regeldateien

Eingebunden in `conf/coraza-spoa.yaml`; danach `custom/*.conf`.
