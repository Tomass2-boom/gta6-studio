"""Capture-only live mode for the Windows node (phase 2). No Whisper, no judge, no render, no database.

  chat files (written by studio.chatlog) --tail--> ChatDetector --spike--> cut video --> data/outbox/moments/

* It does not open any chat connection itself: chatlog stays the only chat writer, so a crash here never loses chat
  and nothing is logged twice. It tails data/chat/*.jsonl.
* Video: only the busiest `node.max_streams` Tier A streams that are live AND playing GTA 6 are recorded (the link is
  the limit, W1 survey). A spike on any other Twitch channel is fetched from Twitch's rewind (Tier B way).
* Output per moment, exactly as the contract in docs/windows-node.md section C says:
  moments/<node>-<channel>-<int spike epoch>.mp4 (written as .mp4.part, renamed when complete) and a .json with the
  same name written LAST. The Mac imports only complete pairs.

Run by the supervisor when node.capture is true:  python -m studio.node.capture
"""
import asyncio
import json
import logging
import os
import time
from collections import deque
from datetime import date, datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path

from ..config import DATA, load
from ..detect import signals
from ..ingest import game as gta6
from ..ingest import live as vid
from ..live import Live, guard, resolve_channels
from ..render.layout import probe
from .proc import kill_tree
from .supervisor import APP, OUT, atomic_write, cfg, live_channels

CHAT = DATA / "chat"
PREFIX = {"twitch": "", "kick": "kick-", "youtube": "yt-"}


def chat_path(chat_dir: Path, platform: str, channel: str, day: date) -> Path:
    return chat_dir / f"{PREFIX[platform]}{channel}-{day}.jsonl"


class ChatTail:
    """Follows today's chat file of every channel and hands each new line to on_message(channel, t, user, text).
    Lines that were already there when the tail started are skipped (history is not a live burst)."""

    def __init__(self, chans: dict, chat_dir: Path, on_message):
        self.chans, self.dir, self.on_message = chans, chat_dir, on_message
        self.pos: dict[Path, int] = {}
        self.first_pass = True

    def poll(self, today: date | None = None) -> int:
        today, n = today or date.today(), 0
        for ch, e in self.chans.items():
            p = chat_path(self.dir, e["platform"], ch, today)
            try:
                size = p.stat().st_size
            except OSError:
                continue
            if p not in self.pos:
                self.pos[p] = size if self.first_pass else 0         # a file born after startup is read from its start
            if size < self.pos[p]:                                   # truncated or replaced
                self.pos[p] = 0
            if size == self.pos[p]:
                continue
            with open(p, "rb") as f:
                f.seek(self.pos[p])
                data = f.read(size - self.pos[p])
            end = data.rfind(b"\n") + 1                              # only whole lines; a half-written one waits
            self.pos[p] += end
            for ln in data[:end].decode("utf-8", "replace").splitlines():
                try:
                    d = json.loads(ln)
                    self.on_message(ch, float(d["t"]), d.get("u", ""), d["m"])
                    n += 1
                except (ValueError, KeyError, TypeError):
                    continue
        self.first_pass = False
        return n


def choose(prev: set[str], ranked: list[str], n: int, slack: int = 2) -> set[str]:
    """Which channels to record. `ranked` = live GTA 6 Tier A channels, busiest chat first. A channel already being
    recorded keeps its slot until it drops more than `slack` places below the cut (no flapping between two equals)."""
    keep = [c for c in ranked[:n + slack] if c in prev][:n]
    for c in ranked:
        if len(keep) >= n:
            break
        if c not in keep:
            keep.append(c)
    return set(keep)


def live_games(vods: Path, now: float, max_age_s: float = 900) -> dict[str, str]:
    """{channel: game} for streams vodwatch saw live in the last max_age_s."""
    out = {}
    for ch in live_channels(vods, now, max_age_s):
        out[ch] = ""
    try:
        for ln in vods.read_text(encoding="utf-8", errors="replace").splitlines()[-400:]:
            r = json.loads(ln)
            if r.get("channel") in out:
                out[r["channel"]] = r.get("game") or ""
    except (OSError, ValueError):
        pass
    return out


class Capture(Live):
    def __init__(self, chans, lg, node: dict):
        super().__init__(chans, None, lg)
        self.node = node
        self.tier0 = {c: e["tier"] for c, e in self.chans.items()}
        self.selected: set[str] = set()
        self.activity: dict[str, deque] = {c: deque() for c in self.chans}
        self.tail = ChatTail(self.chans, CHAT, self.on_message)
        self.moments = OUT / "moments"
        self.moments.mkdir(parents=True, exist_ok=True)

    # ---- chat ------------------------------------------------------------
    def on_message(self, channel, ts, user, text):
        a = self.activity[channel]
        a.append(ts)
        while a and a[0] < ts - 300:
            a.popleft()
        super().on_message(channel, ts, user, text)

    async def tail_chat(self):
        while True:
            await asyncio.sleep(1)
            await asyncio.to_thread(self.tail.poll)

    # ---- which streams get video -----------------------------------------
    def select(self, now: float):
        games = live_games(OUT / "vods.jsonl", now)
        cand = [c for c, g in games.items() if self.tier0.get(c) == "A" and gta6.matches(g, self.chans[c]["platform"]) is not False]
        cand.sort(key=lambda c: -len(self.activity[c]))
        new = choose(self.selected, cand, int(self.node.get("max_streams", 3)))
        if new != self.selected:
            self.lg.info("recording %s (live GTA 6 candidates by chat: %s)", sorted(new), cand[:8])
        self.selected = new
        for c, e in self.chans.items():          # a spike on a channel without a recorder falls back to Twitch's rewind
            e["tier"] = "A" if (self.tier0[c] == "A" and (c in new or e["platform"] != "twitch")) else "B"

    async def selector(self):
        while True:
            try:
                self.select(time.time())
            except Exception:
                self.lg.exception("selection failed (continuing)")
            await asyncio.sleep(60)

    async def supervise(self, ch):
        """Tier A recorder, only while the channel is selected. Offline channels are retried (slowly)."""
        while True:
            if ch not in self.selected:
                await asyncio.sleep(10)
                continue
            p = vid.start_recording(self.chans[ch]["platform"], ch, self.L["chunk_s"], self.quality(ch))
            self.recorders[ch] = p
            t0 = time.time()
            while p.poll() is None and ch in self.selected:
                await asyncio.sleep(5)
            if p.poll() is None:
                kill_tree(p.pid)
            self.lg.info("%s recorder stopped (%s)", ch, "deselected" if ch not in self.selected else "offline or ended")
            await asyncio.sleep(2 if time.time() - t0 > 120 else 60)

    def recording_now(self) -> list[str]:
        now = time.time()
        out = []
        for ch, p in self.recorders.items():
            if p.poll() is None and ch in self.selected:
                cs = vid.chunks(ch, now - 6 * self.L["chunk_s"], now, self.L["chunk_s"])
                if cs:
                    out.append(ch)
        return sorted(out)

    async def beat_writer(self):
        while True:
            try:
                rec = await asyncio.to_thread(self.recording_now)
                atomic_write(APP / "recording.json", json.dumps({"t": time.time(), "channels": rec}))
                atomic_write(APP / "capture.beat", str(time.time()))
            except Exception:
                self.lg.exception("beat failed (continuing)")
            await asyncio.sleep(15)

    # ---- spike -> outbox pair --------------------------------------------
    def make_moment(self, ch, p, entry):
        e, L = self.chans[ch], self.L
        ok, game = gta6.is_gta6_live(e["platform"], ch)
        if not ok:
            self.lg.info("%s: not GTA 6 right now (%s): spike skipped", ch, game)
            return
        first, last = p["first"], p["last"]
        spikes = p["spikes"] + entry["extra"]
        t0, tier = time.time(), e["tier"]
        sig = {"meta": {"spike_wall": [round(s.t) for s in spikes], "hype_share": round(max(s.hype_share for s in spikes), 2),
                        "tier": tier, "detected_at": first, "node": self.node.get("name", "win")}}
        for s in spikes:
            sig[s.kind] = round(max(sig.get(s.kind, 0), s.strength), 2)
        try:
            seg = vid.SEG / f"{ch}-{int(first)}.mp4"
            if tier == "A":
                path, base, dur = vid.cut_from_recording(ch, first - L["pre_roll_s"], last + L["post_roll_s"], first, seg,
                                                         L["chunk_s"], L["min_len_s"], L.get("hole_s", {}).get(e["platform"]))
            else:
                if not self.rewinds.acquire(blocking=False):
                    raise RuntimeError(f"skipped: {L.get('max_rewinds', 2)} rewinds already downloading (bandwidth)")
                try:
                    path, base, dur = vid.fetch_rewind(e["platform"], ch, L["tier_b_duration_s"], seg, L["min_len_s"],
                                                       self.quality(ch))
                finally:
                    self.rewinds.release()
            sig["meta"]["video_from"] = base
            try:
                pk = signals.loud_peaks(signals.loudness_per_second(str(path)), z=L["loud_z"])
                if pk:
                    sig["loudness"] = round(max(s for _, s in pk), 2)
            except Exception:
                self.lg.exception("loudness check failed for %s (continuing without)", ch)
            score = len([k for k in sig if k != "meta"]) * 10 + sum(v for k, v in sig.items() if k != "meta")
            try:
                height = probe(str(path))[1]
            except Exception:
                height = None
            self.publish(ch, e, path, base, dur, first, sig, score, height)
            self.lg.info("moment %s-%s-%d: %.0fs of video (%s) in %.0fs, in the outbox", self.node.get("name", "win"), ch,
                         int(first), dur, tier, time.time() - t0)
        except Exception as ex:   # no silent failures
            self.lg.exception("%s cut failed: %s", ch, ex)

    def publish(self, ch, e, path, base, dur, first, sig, score, height):
        """mp4 as .part, renamed when complete, then the .json LAST (the Mac imports only complete pairs)."""
        node = self.node.get("name", "win")
        mid = f"{node}-{ch}-{int(first)}"
        mp4 = self.moments / f"{mid}.mp4"
        part = mp4.with_name(mp4.name + ".part")
        os.replace(path, part)
        os.replace(part, mp4)
        url = vid.stream_url(e["platform"], ch)
        atomic_write(mp4.with_suffix(".json"), json.dumps({
            "id": mid, "node": node, "streamer": e["name"], "channel": ch, "platform": e["platform"],
            "url": f"{url}#live={int(first)}", "stream_url": url,
            "t_start": base, "t_end": base + dur, "spike_t": int(first), "signals": sig, "signal_score": score,
            "quality": self.quality(ch), "height": height, "created": int(time.time())}))

    # ---- main ------------------------------------------------------------
    async def main(self):
        tasks = [guard("loop", self.loop, self.lg), guard("tail", self.tail_chat, self.lg),
                 guard("selector", self.selector, self.lg), guard("beat", self.beat_writer, self.lg)]
        tasks += [guard(c, lambda c=c: self.supervise(c), self.lg) for c, e in self.chans.items() if self.tier0[c] == "A"]
        tasks += [guard(c, lambda c=c: self.watch_loudness(c), self.lg) for c in self.chans if self.uses_loud(c)]
        await asyncio.gather(*tasks)


def main():
    (DATA / "logs").mkdir(parents=True, exist_ok=True)
    h = RotatingFileHandler(DATA / "logs" / "capture.log", maxBytes=5_000_000, backupCount=5, encoding="utf-8")
    h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logging.basicConfig(handlers=[h], level=logging.INFO)
    lg = logging.getLogger("capture")
    chans = resolve_channels(None, lg)
    c = Capture(chans, lg, cfg())
    lg.info("capture-only: %d channels, tier A %s, max %s streams", len(chans),
            [x["channel"] for x in chans if x["tier"] == "A"], c.node.get("max_streams", 3))
    try:
        asyncio.run(c.main())
    finally:
        for p in c.recorders.values():
            kill_tree(p.pid)


if __name__ == "__main__":
    main()
