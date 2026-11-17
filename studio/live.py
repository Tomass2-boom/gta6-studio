"""Live mode. Chat spike -> cut/fetch video -> moment in the DB -> transcribe -> judge -> render.

Chat for all channels is read over one anonymous Twitch IRC connection. Tier A channels are recorded
continuously (chunks named by wall-clock time); Tier B channels are fetched from Twitch's rewind at the spike.
Twitch, Kick and YouTube can be recorded. Channels without a chat reader (see CHAT_READERS) get a loudness
trigger on their own recording instead (detect/loud.py).
"""
import asyncio
import json
import queue
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import typer

from . import db
from .chat import twitch, youtube
from .config import DATA, load
from .detect import signals
from .detect.chat import ChatDetector
from .detect.loud import LoudDetector
from .ingest import live as vid


# Platforms whose chat is the ONLY trigger (the loudness trigger is then off, loud_trigger: no_chat). YouTube has a
# chat reader but is left out on purpose: its reader depends on yt-dlp, so loudness remains a second trigger.
CHAT_READERS = {"twitch"}
PLATFORMS = {"twitch", "kick", "youtube"}


def resolve_channels(arg: str | None, lg) -> list[dict]:
    """[{name, channel, platform, tier}] from --channels a:A,b:B or from streamers.yaml."""
    s = load("streamers")
    block = {str(x).lower() for x in s.get("blocklist", [])}
    out = []
    if arg:
        for it in arg.split(","):
            ch, _, tier = it.strip().partition(":")
            plat, _, ch = ch.rpartition("/")          # kick/destiny:A, youtube/IShowSpeed:A, lirik:B (= Twitch)
            out.append({"name": ch, "channel": ch.lower(), "platform": (plat or "twitch").lower(),
                        "tier": (tier or "B").upper()})
    else:
        for e in s.get("streamers", []):
            ch = e.get("channel") or (e.get("url") or "").rstrip("/").split("/")[-1] or e["name"]
            out.append({"name": e["name"], "channel": ch.lower(), "platform": e.get("platform", "twitch"),
                        "tier": str(e.get("tier", "B")).upper(), "live": e.get("live", True)})
    keep = []
    for e in out:
        if e["name"].lower() in block or e["channel"] in block:
            lg.warning("%s is on the blocklist: not watched", e["name"])
        elif e.get("live") is False:
            lg.info("%s has live: false (VOD test source): not watched", e["name"])
        elif e["platform"] not in PLATFORMS:
            lg.warning("%s: unknown platform %r, skipped", e["name"], e["platform"])
        else:
            if e["platform"] != "twitch" and e["tier"] != "A":
                lg.warning("%s (%s): no rewind for Tier B yet, recording it continuously (Tier A)", e["name"], e["platform"])
                e["tier"] = "A"
            keep.append(e)
    return keep


class Live:
    def __init__(self, chans: list[dict], model: str | None, lg):
        self.cfg = load("settings")
        self.L = self.cfg["live"]
        self.lg, self.model = lg, model
        self.chans = {c["channel"]: c for c in chans}
        self.det = ChatDetector(self.L["chat"])
        self.loud = LoudDetector(self.L["loud"])
        self.loud_done: dict[str, float] = {}   # channel -> start of the last chunk already measured
        self.pending: dict[str, dict] = {}   # channel -> spike group waiting for its video
        self.recent: dict[str, list] = {}    # channel -> moments already cut: [{lo, hi, mid, extra}]
        self.todo: queue.Queue = queue.Queue()
        self.pool = ThreadPoolExecutor(4)
        self.recorders: dict[str, object] = {}

    # ---- chat -> spikes -------------------------------------------------
    def on_message(self, channel, ts, user, text):
        for sp in self.det.feed(channel, ts, text):
            self.on_spike(sp)

    def on_spike(self, sp):
        ch, L = sp.channel, self.L
        self.lg.info("SPIKE(%s) %s x%.1f (burst %.2f vs normal %.2f, hype %.0f%%)", sp.kind, ch, sp.strength,
                     sp.burst, sp.baseline, 100 * sp.hype_share)
        for r in self.recent.get(ch, []):          # same moment, already cut: merge
            if r["lo"] <= sp.t <= r["hi"] + L["merge_s"]:
                r["extra"].append(sp)
                if r.get("mid"):
                    self._add_signal(r["mid"], sp)
                return
        p = self.pending.get(ch)
        if p and sp.t - p["last"] <= L["merge_s"] and sp.t - p["first"] < L["max_window_s"] - L["pre_roll_s"]:
            p["last"] = sp.t
            p["spikes"].append(sp)
            return
        self.pending[ch] = {"first": sp.t, "last": sp.t, "spikes": [sp], "sent": False}

    def _add_signal(self, mid, sp):
        con = db.connect()
        row = con.execute("SELECT signals FROM moments WHERE id=?", (mid,)).fetchone()
        s = json.loads(row["signals"] or "{}")
        s[sp.kind] = max(s.get(sp.kind, 0), sp.strength)
        s.setdefault("meta", {}).setdefault("merged_spikes", []).append(round(sp.t))
        db.update(con, mid, signals=s)

    def dispatch_due(self):
        now, L = time.time(), self.L
        for ch, p in list(self.pending.items()):
            tier = self.chans[ch]["tier"]
            # Tier A waits for the post-roll after the LAST spike; Tier B must grab the rewind NOW.
            due = now >= p["last"] + L["post_roll_s"] if tier == "A" else not p["sent"]
            if not due:
                continue
            del self.pending[ch]
            entry = {"lo": p["first"] - L["pre_roll_s"], "hi": p["last"] + L["post_roll_s"], "mid": None, "extra": []}
            if tier == "B":
                entry["hi"] = p["first"] + L["tier_b_duration_s"] - L["pre_roll_s"]
            self.recent.setdefault(ch, []).append(entry)
            self.pool.submit(self.make_moment, ch, p, entry)

    # ---- video -> moment ------------------------------------------------
    def make_moment(self, ch, p, entry):
        e, L = self.chans[ch], self.L
        con = db.connect()
        first, last = p["first"], p["last"]
        spikes = p["spikes"] + entry["extra"]
        url = f"{vid.stream_url(e['platform'], ch)}#live={int(first)}"
        sig = {"meta": {
            "spike_wall": [round(s.t) for s in spikes], "hype_share": round(max(s.hype_share for s in spikes), 2),
            "tier": e["tier"], "detected_at": first}}
        for s in spikes:   # chat / loud: the strongest spike of each kind
            sig[s.kind] = round(max(sig.get(s.kind, 0), s.strength), 2)
        title = f"{e['name']} live {datetime.fromtimestamp(first, timezone.utc):%Y-%m-%d %H:%MZ}"
        cur = con.execute("INSERT INTO sources(url,platform,streamer,title) VALUES(?,?,?,?)",
                          (url, e["platform"], e["name"], title))
        sid = cur.lastrowid
        con.commit()
        t0 = time.time()
        try:
            out = vid.SEG / f"{ch}-{int(first)}.mp4"
            if e["tier"] == "A":
                path, base, dur = vid.cut_from_recording(ch, first - L["pre_roll_s"], last + L["post_roll_s"],
                                                         first, out, L["chunk_s"], L["min_len_s"])
            else:
                path, base, dur = vid.fetch_rewind(e["platform"], ch, L["tier_b_duration_s"], out, L["min_len_s"])
            con.execute("UPDATE sources SET path=?, duration=? WHERE id=?", (str(path), dur, sid))
            sig["meta"]["video_from"] = base
            try:   # second signal: loud audio inside the segment (both firing = stronger moment)
                pk = signals.loud_peaks(signals.loudness_per_second(str(path)), z=L["loud_z"])
                if pk:
                    sig["loudness"] = round(max(s for _, s in pk), 2)
            except Exception:
                self.lg.exception("loudness check failed for %s (continuing without)", ch)
            score = len([k for k in sig if k != "meta"]) * 10 + sum(v for k, v in sig.items() if k != "meta")
            cur = con.execute("INSERT INTO moments(source_id,t_start,t_end,signals,signal_score) VALUES(?,?,?,?,?)",
                              (sid, 0, dur, json.dumps(sig), score))
            con.commit()
            mid = entry["mid"] = cur.lastrowid
            for sp in entry["extra"]:
                self._add_signal(mid, sp)
            self.lg.info("moment %d: %s cut (%.0fs of video in %.0fs), queued", mid, ch, dur, time.time() - t0)
            self.todo.put((mid, str(path), first))
        except Exception as ex:  # no silent failures
            self.lg.exception("%s cut failed", ch)
            cur = con.execute("INSERT INTO moments(source_id,t_start,t_end,signals,state,fail_reason) "
                              "VALUES(?,?,?,?,'failed',?)", (sid, 0, 0, json.dumps(sig), f"video: {ex}"))
            con.commit()

    def worker(self):
        """One moment at a time: Whisper and the LLM share the same GPU."""
        from .pipeline import process
        con = db.connect()
        while True:
            mid, path, spike_t = self.todo.get()
            ok = process(con, mid, path, self.lg, self.model)
            self.lg.info("moment %d %s, %.0fs after the chat spike", mid, "CLIP READY" if ok else "no clip",
                         time.time() - spike_t)

    # ---- main loop ------------------------------------------------------
    async def supervise(self, ch):
        """Tier A: keep the recorder running; an offline channel is retried every minute."""
        while True:
            p = vid.start_recording(self.chans[ch]["platform"], ch, self.L["chunk_s"])
            self.recorders[ch] = p
            t0 = time.time()
            while p.poll() is None:
                await asyncio.sleep(5)
            self.lg.info("%s recorder stopped (offline?)", ch)
            await asyncio.sleep(2 if time.time() - t0 > 120 else 60)

    def uses_loud(self, ch) -> bool:
        mode, e = self.L.get("loud_trigger", "no_chat"), self.chans[ch]
        return e["tier"] == "A" and (mode == "always" or (mode == "no_chat" and e["platform"] not in CHAT_READERS))

    def measure_new_chunks(self, ch):
        """Blocking (ffmpeg): loudness of each finished chunk not measured yet -> spikes."""
        L, out = self.L, []
        now = time.time()
        done = self.loud_done.get(ch)
        for t, path in vid.chunks(ch, now - 3 * L["chunk_s"] if done is None else done, now, L["chunk_s"]):
            if done is not None and t <= done:
                continue
            try:
                rms = signals.loudness_per_second(str(path))
            except Exception:
                self.lg.warning("%s: loudness of %s failed, skipped", ch, path.name)
                rms = []
            if len(rms):
                out += self.loud.feed(ch, t, rms)
            self.loud_done[ch] = t
        return out

    async def watch_loudness(self, ch):
        while True:
            await asyncio.sleep(self.L["chunk_s"] / 2)
            try:
                for sp in await asyncio.to_thread(self.measure_new_chunks, ch):
                    self.on_spike(sp)
            except Exception:
                self.lg.exception("%s loudness watcher error (continuing)", ch)

    async def loop(self):
        n = 0
        while True:
            await asyncio.sleep(2)
            for sp in self.det.tick(time.time()):
                self.on_spike(sp)
            self.dispatch_due()
            n += 1
            if n % 1800 == 0:
                self.lg.info("deleted %d old raw files", vid.cleanup(self.L["keep_h"]))

    async def main(self):
        tw = [c for c, e in self.chans.items() if e["platform"] == "twitch"]
        tasks = ([twitch.read(tw, self.on_message, self.lg)] if tw else []) + [self.loop()]
        tasks += [youtube.read_one(c, vid.stream_url("youtube", c), self.on_message, self.lg)
                  for c, e in self.chans.items() if e["platform"] == "youtube"]
        tasks += [self.supervise(c) for c, e in self.chans.items() if e["tier"] == "A"]
        tasks += [self.watch_loudness(c) for c in self.chans if self.uses_loud(c)]
        threading.Thread(target=self.worker, daemon=True).start()
        await asyncio.gather(*tasks)


def run(channels: str | None, model: str | None):
    from .cli import log
    lg = log("live")
    chans = resolve_channels(channels, lg)
    if not chans:
        raise typer.Exit("No channels to watch. Fill config/streamers.yaml (S5) or pass --channels a:A,b:B")
    lg.info("watching %s", ", ".join(f"{c['name']}(tier {c['tier']})" for c in chans))
    lv = Live(chans, model, lg)
    try:
        asyncio.run(lv.main())
    except KeyboardInterrupt:
        pass
    finally:
        import os, signal
        for p in lv.recorders.values():
            try:
                os.killpg(p.pid, signal.SIGTERM)
            except Exception:
                pass
