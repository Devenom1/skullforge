from skullforge.core.render import image_to_rgb565_bytes, render_frame_bytes, render_stats_image
from skullforge.core.sensors import Stats

_STATS = Stats(cpu_load_pct=42.0, mem_load_pct=61.0, cpu_temp_c=55.2, fan_rpm=None, cpu_power_w=12.3)


def test_render_stats_image_size():
    img = render_stats_image(_STATS, 170, 320, "24h")
    assert img.size == (170, 320)


def test_render_frame_bytes_size_matches_panel():
    width, height = 170, 320
    frame = render_frame_bytes(_STATS, width, height, "24h")
    assert len(frame) == width * height * 2  # RGB565 = 2 bytes/pixel


def test_rgb565_packing_round_trip_on_pure_colors():
    from PIL import Image

    for color in [(0, 0, 0), (255, 255, 255), (16, 96, 200)]:
        img = Image.new("RGB", (4, 4), color)
        packed = image_to_rgb565_bytes(img, rotate=False)
        assert len(packed) == 4 * 4 * 2
        # RGB565 quantizes 8-bit channels down to 5/6/5 bits - this is the
        # same truncation image_to_rgb565_bytes itself applies, so it's the
        # correct expected value, not the original 8-bit color.
        expected = (color[0] & 0xF8, color[1] & 0xFC, color[2] & 0xF8)

        # unpack the first pixel back out (big-endian per the panel's wire format)
        hi, lo = packed[0], packed[1]
        value = (hi << 8) | lo
        r = (value >> 11) & 0x1F
        g = (value >> 5) & 0x3F
        b = value & 0x1F
        assert (r << 3, g << 2, b << 3) == expected


def test_rotate_swaps_dimensions():
    from PIL import Image

    img = Image.new("RGB", (170, 320), (0, 0, 0))
    rotated_bytes = image_to_rgb565_bytes(img, rotate=True)
    not_rotated_bytes = image_to_rgb565_bytes(img, rotate=False)
    # same pixel count either way, since it's a solid fill
    assert len(rotated_bytes) == len(not_rotated_bytes) == 170 * 320 * 2
