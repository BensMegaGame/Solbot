"""Blockchain-Bausteine fuer den Live-Bot: Wallet laden, Kontostaende lesen, Jupiter-Swaps bauen, signieren,
simulieren, senden und bestaetigen, leere Token-Konten schliessen. Keine Strategie-Logik hier."""
import base64, json, os, time
import requests
from solders.keypair import Keypair
from solders.pubkey import Pubkey
from solders.transaction import VersionedTransaction
from solders.message import MessageV0
from solders.instruction import Instruction, AccountMeta
from solders.hash import Hash

USDC = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
SOL_MINT = "So11111111111111111111111111111111111111112"
TOKEN_PROGRAMS = ["TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA",      # SPL Token
                  "TokenzQdBNbLqP5VEhdkAS6EPFLC1PE9n9vbDgUEfJp7GEz"]     # Token-2022
JUP = "https://api.jup.ag/swap/v1"
UA = {"User-Agent": "solbot-live/1.0"}
MAX_PRIO_LAMPORTS = 200_000          # Priority-Fee hoechstens 0,0002 SOL je Transaktion (~1-2 Cent)


def lade_wallet(pfad):
    """Privater Schluessel aus Datei: base58 (Phantom-Export) oder JSON-Liste mit 64 Zahlen."""
    s = open(pfad).read().strip()
    if s.startswith("["): return Keypair.from_bytes(bytes(json.loads(s)))
    return Keypair.from_base58_string(s)


class Chain:
    def __init__(self, kp, helius_key, jup_key):
        self.kp = kp; self.pub = str(kp.pubkey())
        self.url = f"https://mainnet.helius-rpc.com/?api-key={helius_key}"
        self.jup_h = {"x-api-key": jup_key, "Content-Type": "application/json", **UA}

    # ---------- RPC ----------
    def rpc(self, method, params):
        for versuch in range(3):
            try:
                r = requests.post(self.url, json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params},
                                  headers=UA, timeout=25)
                if r.status_code == 429: time.sleep(3 * (versuch + 1)); continue
                j = r.json()
                if j.get("error"): raise RuntimeError(f"{method}: {str(j['error'])[:200]}")
                return j.get("result")
            except (requests.RequestException, ValueError):
                if versuch == 2: raise
                time.sleep(2)

    def sol_lamports(self):
        return int(self.rpc("getBalance", [self.pub, {"commitment": "confirmed"}])["value"])

    def token_konten(self):
        """{mint: {"raw": int, "dec": int, "konten": [(adresse, programm, raw)]}} fuer beide Token-Programme."""
        out = {}
        for prog in TOKEN_PROGRAMS:
            res = self.rpc("getTokenAccountsByOwner", [self.pub, {"programId": prog},
                                                       {"encoding": "jsonParsed", "commitment": "confirmed"}])
            for acc in (res or {}).get("value") or []:
                info = acc["account"]["data"]["parsed"]["info"]
                m = info["mint"]; ta = info["tokenAmount"]; raw = int(ta["amount"])
                e = out.setdefault(m, {"raw": 0, "dec": int(ta["decimals"]), "konten": []})
                e["raw"] += raw; e["konten"].append((acc["pubkey"], prog, raw))
        return out

    def blockhoehe(self):
        return int(self.rpc("getBlockHeight", [{"commitment": "confirmed"}]))

    def status(self, sig):
        """'ok' | 'fehler:<grund>' | None (noch unbekannt)."""
        v = (self.rpc("getSignatureStatuses", [[sig], {"searchTransactionHistory": True}]) or {}).get("value") or [None]
        s = v[0]
        if not s: return None
        if s.get("err") is not None: return f"fehler:{json.dumps(s['err'])[:120]}"
        if s.get("confirmationStatus") in ("confirmed", "finalized"): return "ok"
        return None

    # ---------- Jupiter ----------
    def quote(self, ein, aus, raw, slippage_bps):
        """Volle Jupiter-Quote (dict) oder None."""
        if raw <= 0: return None
        try:
            r = requests.get(f"{JUP}/quote", headers=self.jup_h, timeout=15,
                             params={"inputMint": ein, "outputMint": aus, "amount": int(raw), "slippageBps": int(slippage_bps),
                                     "restrictIntermediateTokens": "true"})
            if r.status_code != 200: return None
            q = r.json()
            return q if q.get("outAmount") else None
        except (requests.RequestException, ValueError):
            return None

    def swap_tx(self, q):
        """Signierte Swap-Transaktion (VersionedTransaction, lastValidBlockHeight) oder Exception."""
        r = requests.post(f"{JUP}/swap", headers=self.jup_h, timeout=25, json={
            "quoteResponse": q, "userPublicKey": self.pub, "wrapAndUnwrapSol": True, "dynamicComputeUnitLimit": True,
            "prioritizationFeeLamports": {"priorityLevelWithMaxLamports": {"maxLamports": MAX_PRIO_LAMPORTS,
                                                                          "priorityLevel": "high"}}})
        if r.status_code != 200: raise RuntimeError(f"jupiter swap {r.status_code}: {r.text[:150]}")
        j = r.json()
        if j.get("simulationError"): raise RuntimeError(f"jupiter-simulation: {str(j['simulationError'])[:150]}")
        unsigniert = VersionedTransaction.from_bytes(base64.b64decode(j["swapTransaction"]))
        tx = VersionedTransaction(unsigniert.message, [self.kp])
        return tx, int(j.get("lastValidBlockHeight") or 0)

    def simulieren(self, tx):
        """None = Simulation ok, sonst Fehlertext."""
        res = self.rpc("simulateTransaction", [base64.b64encode(bytes(tx)).decode(),
                                               {"encoding": "base64", "sigVerify": True, "commitment": "confirmed"}])
        err = ((res or {}).get("value") or {}).get("err")
        return None if err is None else json.dumps(err)[:200]

    def senden(self, tx):
        """Sendet mit Vorab-Pruefung (Preflight). Gibt die Signatur zurueck oder wirft eine Exception."""
        return self.rpc("sendTransaction", [base64.b64encode(bytes(tx)).decode(),
                                            {"encoding": "base64", "skipPreflight": False, "preflightCommitment": "confirmed"}])

    def warten(self, sig, last_valid, max_s=50):
        """Wartet bis max_s Sekunden. 'ok' | 'fehler:..' | 'abgelaufen' | None (noch offen)."""
        ende = time.time() + max_s
        while time.time() < ende:
            s = self.status(sig)
            if s: return s
            time.sleep(2.5)
        return self.endgueltig(sig, last_valid)

    def endgueltig(self, sig, last_valid):
        s = self.status(sig)
        if s: return s
        if last_valid and self.blockhoehe() > last_valid + 5: return "abgelaufen"
        return None

    # ---------- leere Token-Konten schliessen (Miete ~0,002 SOL zurueck) ----------
    def konto_schliessen(self, konto, programm):
        me = self.kp.pubkey()
        ix = Instruction(Pubkey.from_string(programm), bytes([9]),            # 9 = CloseAccount
                         [AccountMeta(Pubkey.from_string(konto), False, True), AccountMeta(me, False, True),
                          AccountMeta(me, True, False)])
        bh = self.rpc("getLatestBlockhash", [{"commitment": "confirmed"}])["value"]
        msg = MessageV0.try_compile(me, [ix], [], Hash.from_string(bh["blockhash"]))
        tx = VersionedTransaction(msg, [self.kp])
        return self.senden(tx)
