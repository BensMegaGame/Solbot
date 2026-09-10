# Paper-Trading: Bot A & Bot B

Zwei simulierte Trading-Bots (je 500 $ Startkapital), die kostenlos auf GitHub laufen.
Kein Server, kein PC, der an bleiben muss. Dashboard für dich und Freunde über GitHub Pages.

- **Bot A** – Micro-Cap-Lotterie auf Solana (Screener + feste Ausstiegsregeln, 50 $ pro Ticket)
- **Bot B** – langsame Strategie auf großen Tokens (SOL, JUP, JTO, PYTH, RENDER, RAY, KMNO, BTC, ETH), Strategie wird per Backtest festgelegt

## Einrichten (einmalig, ca. 15 Minuten)

1. **GitHub-Account** anlegen (github.com, kostenlos), falls noch nicht vorhanden.
2. **Neues Repository** erstellen: oben rechts `+` → *New repository* → Name z.B. `solbot` → *Public* (für Pages nötig, sonst kostenpflichtig) → *Create repository*.
3. **Dateien hochladen**: Im leeren Repo auf *uploading an existing file* klicken, alle Dateien und Ordner aus diesem Paket per Drag & Drop hineinziehen (auch den versteckten Ordner `.github` – auf dem Mac ggf. mit Cmd+Shift+. einblenden). Unten *Commit changes*.
   - Falls der Browser den `.github`-Ordner nicht mitnimmt: Im Repo *Add file → Create new file*, als Dateinamen `.github/workflows/run.yml` eintippen (die Schrägstriche legen die Ordner an), Inhalt einfügen, Commit. Das Gleiche für `backtest.yml`.
4. **Actions erlauben**: Reiter *Settings → Actions → General* → unter *Workflow permissions* auf **Read and write permissions** stellen → *Save*.
5. **Dashboard einschalten**: *Settings → Pages* → unter *Build and deployment* → Source: *Deploy from a branch* → Branch `main`, Ordner `/ (root)` → *Save*. Nach 1–2 Minuten steht oben die Adresse, z.B. `https://DEINNAME.github.io/solbot/`. Das ist der Link für deine Freunde.
6. **Backtest starten**: Reiter *Actions* → links *backtest-bot-b* → *Run workflow* → *Run workflow*. Dauert ca. 2 Minuten. Ergebnis erscheint im Dashboard unter „Backtest Bot B“ und legt automatisch die beste Strategie fest.
7. **Paper-Bots starten**: *Actions → paper-bots → Run workflow*. Ab dann läuft es automatisch alle 30 Minuten.

## Was du danach siehst

Im Dashboard: Kapitalverlauf beider Bots, offene Positionen (mit Link zu DexScreener), letzte Trades mit Grund (`tp1`, `stop`, `trail`, `time`…), Backtest-Ergebnis mit Vergleich zu Kaufen & Halten.

Rohdaten liegen im Ordner `data/`:
- `bot_a_state.json`, `bot_b_state.json` – Portfolio, Trades, Kapitalkurve
- `bot_a_candidates.jsonl` – **alle** gesehenen Micro-Cap-Kandidaten (auch nicht gekaufte) → Grundlage für den späteren ehrlichen Backtest von Bot A
- `bot_a_signals.jsonl` – Kandidaten, die alle Filter bestanden haben
- `backtest_b.json`, `strategy_b.json` – Backtest-Ergebnis und gewählte Strategie

## Anpassen

- Filter für Bot A: oben in `bot_a.py` (Alter, Liquidität, MCap, Ausstiegsregeln)
- Strategien für Bot B: `strategies_b.py` – neue Variante hinzufügen, Backtest erneut laufen lassen
- Strategie manuell festlegen: `data/strategy_b.json` bearbeiten, z.B. `{"strategy": "V2_meanrev"}`
- Alles zurücksetzen: die `*_state.json` im Ordner `data/` löschen
- Dateien kannst du direkt im Browser auf GitHub bearbeiten (Stift-Symbol), auch vom Handy.

## Optional: CoinGecko-Key

Die kostenlose CoinGecko-API hat ein knappes Limit. Wenn der Backtest oder Bot B mit „429“ abbricht: auf coingecko.com einen kostenlosen *Demo API Key* holen und im Repo unter *Settings → Secrets and variables → Actions → New repository secret* als `COINGECKO_KEY` eintragen.

## Grenzen (ehrlich)

- GitHub startet geplante Läufe nicht sekundengenau; Verzögerungen von Minuten sind normal und für diese Strategien unproblematisch.
- Bot A braucht etwa 4 Wochen, bis seine Zahlen etwas aussagen (wenige Kandidaten pro Tag).
- Slippage ist geschätzt (Ordergröße / Poolliquidität). Bei echten Trades kann sie höher sein.
- Der Backtest von Bot B kennt keine Unlock-Termine (dafür gibt es keine kostenlose API); V2 nutzt stattdessen Kursrückgänge als Auslöser.

## Später: echte Trades

Alle simulierten Käufe/Verkäufe laufen durch `Paper.buy()` und `Paper.sell()` in `common.py`. Für echte Trades werden nur diese beiden Funktionen durch Jupiter-Swap-Aufrufe ersetzt; Strategien, Datensammlung und Dashboard bleiben gleich. Erst sinnvoll, wenn der Paper-Modus über mindestens 8 Wochen nach Gebühren positiv ist.
