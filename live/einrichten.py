"""Einrichtung von Bot E LIVE - Schritt fuer Schritt, fragt alles ab. Kann gefahrlos mehrmals gestartet werden.

Aufruf im Ordner live der Live-Kopie (mit aktivem venv):
    python einrichten.py
Macht:
  1) Wallet-Schluessel aus Phantom in eine geschuetzte Datei schreiben (nur root darf sie lesen) und die Adresse zeigen
  2) Telegram einrichten (Token abfragen, Chat-ID automatisch finden, Testnachricht)
  3) Fehlende Eintraege in der .env ergaenzen (Modus trocken, max. 20 $ je Kauf)
  4) Cronjob eintragen (alle 2 Minuten)
Es wird NICHTS gekauft oder verkauft.
"""
import os, sys, getpass, json, subprocess, time

HIER = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HIER))
ENV = os.path.join(ROOT, ".env")
KEY_STD = os.path.join(ROOT, ".solbot_live_key")


def env_lesen():
    d = {}
    if os.path.exists(ENV):
        for z in open(ENV):
            z = z.strip()
            if z.startswith("export "): z = z[7:]
            if "=" in z and not z.startswith("#"):
                k, v = z.split("=", 1); d[k.strip()] = v.strip().strip('"').strip("'")
    return d

def env_setzen(k, v):
    zeilen = open(ENV).read().splitlines() if os.path.exists(ENV) else []
    neu, gefunden = [], False
    for z in zeilen:
        if z.strip().split("=", 1)[0].replace("export ", "").strip() == k:
            neu.append(f"{k}={v}"); gefunden = True
        else: neu.append(z)
    if not gefunden: neu.append(f"{k}={v}")
    open(ENV, "w").write("\n".join(neu) + "\n")
    os.chmod(ENV, 0o600)

def ja(frage):
    return input(frage + " (ja/nein): ").strip().lower() in ("ja", "j", "yes", "y")


def main():
    print("=== Einrichtung Bot E LIVE ===\n")
    print(f"Live-Ordner: {HIER}\nHauptordner: {ROOT}\n.env:        {ENV}\n")
    try:
        from solders.keypair import Keypair  # noqa
    except ImportError:
        sys.exit("Das Paket solders fehlt. Erst ausfuehren:  pip install solders   (mit aktivem venv)")
    sys.path.insert(0, HIER)
    from chain import lade_wallet

    env = env_lesen()
    fehlen = [k for k in ("HELIUS_KEY", "JUP_KEY") if not env.get(k)]
    if fehlen: sys.exit(f"In der .env fehlt: {', '.join(fehlen)} - die braucht auch der Live-Bot.")
    if not env.get("CODEX_KEY"): print("Hinweis: CODEX_KEY fehlt - dann nutzt der Bot das Coin-Universum von E Paper.\n")

    # 1) Wallet
    key = env.get("LIVE_KEY_FILE") or KEY_STD
    print("--- 1) Wallet ---")
    if os.path.exists(key) and not ja(f"Schluesseldatei {key} existiert schon. Ersetzen?"):
        pass
    else:
        print("Phantom: Einstellungen -> Konten verwalten -> das NEUE Bot-Konto -> Privaten Schluessel anzeigen.")
        print("Schluessel kopieren und hier einfuegen. Er wird beim Einfuegen NICHT angezeigt (das ist richtig so).")
        s = getpass.getpass("Privater Schluessel: ").strip()
        tmp = key + ".neu"
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f: f.write(s)
        try:
            kp = lade_wallet(tmp)
        except Exception as ex:
            os.remove(tmp); sys.exit(f"Das ist kein gueltiger Schluessel ({ex}). Nichts gespeichert.")
        os.replace(tmp, key); os.chmod(key, 0o600)
        print(f"\nGespeichert in {key} (nur root kann sie lesen).")
    adr = str(lade_wallet(key).pubkey())
    print(f"\nWALLET-ADRESSE: {adr}")
    if not ja("Stimmt das mit der Adresse des Bot-Kontos in Phantom ueberein?"):
        sys.exit("Dann bitte den Schluessel des richtigen Kontos einfuegen und neu starten.")
    env_setzen("LIVE_KEY_FILE", key)

    # 2) Telegram
    print("\n--- 2) Telegram ---")
    import requests
    tok = env.get("TELEGRAM_TOKEN")
    if not tok or ja("Telegram-Token neu eingeben?"):
        tok = input("Token von @BotFather einfuegen: ").strip()
        env_setzen("TELEGRAM_TOKEN", tok)
    chat = env.get("TELEGRAM_CHAT")
    if not chat:
        print("Schreib deinem Bot in Telegram jetzt eine Nachricht (z.B. hallo). Ich warte bis zu 2 Minuten ...")
        for _ in range(24):
            try:
                r = requests.get(f"https://api.telegram.org/bot{tok}/getUpdates", timeout=15).json()
                ids = [u["message"]["chat"]["id"] for u in r.get("result", []) if u.get("message")]
                if ids: chat = str(ids[-1]); break
                if not r.get("ok"): sys.exit(f"Telegram lehnt den Token ab: {r.get('description')}")
            except requests.RequestException:
                pass
            time.sleep(5)
        if not chat: sys.exit("Keine Nachricht gefunden. Bot in Telegram anschreiben und einrichten.py neu starten.")
        env_setzen("TELEGRAM_CHAT", chat)
    ok = requests.post(f"https://api.telegram.org/bot{tok}/sendMessage",
                       data={"chat_id": chat, "text": f"✅ Bot E LIVE: Telegram funktioniert.\nWallet: {adr}"}, timeout=15).status_code == 200
    print("Testnachricht gesendet - schau in Telegram." if ok else "Testnachricht ging NICHT raus - Token/Chat pruefen.")

    # 3) Einstellungen
    print("\n--- 3) Einstellungen ---")
    env = env_lesen()
    if not env.get("LIVE_MODUS"): env_setzen("LIVE_MODUS", "trocken")
    if not env.get("LIVE_MAX_KAUF_USD"): env_setzen("LIVE_MAX_KAUF_USD", "20")
    env = env_lesen()
    print(f"Modus: {env['LIVE_MODUS']} | max. je Kauf: {env['LIVE_MAX_KAUF_USD']} $ (aendern mit: python modus.py)")

    # 4) Cronjob
    print("\n--- 4) Cronjob ---")
    zeile = f"*/2 * * * * bash {os.path.join(HIER, 'run_live.sh')}"
    alt = subprocess.run(["crontab", "-l"], capture_output=True, text=True).stdout
    if "run_live.sh" in alt:
        print("Cronjob ist schon eingetragen.")
    elif ja("Cronjob jetzt eintragen (Bot laeuft dann alle 2 Minuten)?"):
        neu = alt.rstrip("\n") + ("\n" if alt.strip() else "") + zeile + "\n"
        subprocess.run(["crontab", "-"], input=neu, text=True, check=True)
        print("Eingetragen:", zeile)
    print("\nFertig. Der Bot startet im Modus", env["LIVE_MODUS"].upper(), "- du bekommst gleich eine Startnachricht in Telegram.")
    print(f"Wallet erst fuellen, wenn der Trockenlauf gut aussieht: USDC und etwas SOL an {adr}")


if __name__ == "__main__":
    main()
