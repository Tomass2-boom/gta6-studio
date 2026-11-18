"""Usability gate run before every render (user, 2026-11-18): a clip must at least be watchable and postable.
- the facecam must be a real face and not blown up so far that it turns to mush;
- the GTA gameplay must be on screen and not zoomed in so far you can't see what's happening;
- the source must be at least 720p.
It fixes what it can (smaller cam panel, or the full-game layout C) and only refuses what it can't fix.
Thresholds live in style.yaml `checks`."""
from . import layout as L


class Unusable(Exception):
    """The clip can't be made watchable from this source."""


def _cam_upscale(style: dict, w: int, h: int, cam) -> tuple[float, float]:
    """(cam panel upscale factor, game crop width as a share of the source) for layout A at this style."""
    _, geo = L.build("A", style, w, h, cam)
    chh = geo["cam_crop"][1]
    gw = geo["game"][0][2]
    H = style["output"]["height"]
    ch_out = round(H * style["layout_a"]["cam_height"]) // 2 * 2
    return ch_out / max(1, chh), gw / w


def _face_share(src_path: str, a: float, b: float, w: int, h: int, cam) -> float:
    from . import facetrack as T
    _, best = T.faces_over_time(src_path, a, b, w, h, 2.0, cam)
    return sum(f is not None for f in best) / max(1, len(best))


def fit(style: dict, src_path: str, a: float, b: float, w: int, h: int, lay: str, cam, note: str):
    """Return (layout, cam, style, report). Raises Unusable when nothing watchable can be made."""
    c = style.get("checks") or {}
    if not c.get("enabled"):
        return lay, cam, style, {}
    rep = {"source": f"{w}x{h}"}
    if h < c.get("min_source_h", 700):
        raise Unusable(f"source is only {w}x{h} (needs 720p or better)")
    if lay == "A" and cam is not None and c.get("face_in_cam"):
        fs = _face_share(src_path, a, b, w, h, cam)
        rep["face_in_cam"] = round(fs, 2)
        if fs < c["face_in_cam"]:
            lay, cam = "C", None
            rep["fix"] = f"no face in the cam box ({fs:.0%} of samples), so full game (C)"
    if lay == "A" and cam is not None:
        A = style["layout_a"]
        share, lo = A["cam_height"], c.get("min_cam_height", 0.30)
        up, gws = _cam_upscale(style, w, h, cam)
        while up > c.get("max_cam_upscale", 3.0) and share - 0.01 >= lo:
            share = round(share - 0.01, 2)
            style = {**style, "layout_a": {**A, "cam_height": share}}
            up, gws = _cam_upscale(style, w, h, cam)
        rep.update(cam_height=share, cam_upscale=round(up, 2), game_width=round(gws, 2))
        if up > c.get("max_cam_upscale_hard", 4.0):
            lay, cam = "C", None
            rep["fix"] = f"cam too small in the source ({up:.1f}x upscale), so full game (C)"
        elif gws < c.get("min_game_width", 0.42):
            lay, cam = "C", None
            rep["fix"] = f"game too zoomed in ({gws:.0%} of its width), so full game (C)"
    if lay == "B":
        W, H = style["output"]["width"], style["output"]["height"]
        gws = min(1.0, (W / H * h) / w)
        rep["game_width"] = round(gws, 2)
        if gws < c.get("min_game_width", 0.42):
            lay = "C"
            rep["fix"] = f"full-height crop shows only {gws:.0%} of the game's width, so C"
    return lay, cam, style, rep
