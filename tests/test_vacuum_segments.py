"""Tests for vacuum room (segment) cleaning helpers."""

import base64

from custom_components.tuya_local.vacuum import (
    encode_ilife_segments,
    parse_ilife_room_list,
)


def _b64(text):
    return base64.b64encode(text.encode("utf-8")).decode()


def test_parse_room_list():
    raw = _b64("1794503112,mapName,5,Łazienka,0,Salon,2,Pokój dziecięcy")
    assert parse_ilife_room_list(raw) == [
        ("5", "Łazienka"),
        ("0", "Salon"),
        ("2", "Pokój dziecięcy"),
    ]


def test_parse_room_list_invalid():
    assert parse_ilife_room_list(None) == []
    assert parse_ilife_room_list("") == []
    assert parse_ilife_room_list("***") == []
    assert parse_ilife_room_list(_b64("1794503112,mapName")) == []


def test_encode_segments():
    # Values confirmed on a real ILIFE A30 Pro.
    assert encode_ilife_segments(["5"]) == "AQEAAAAg"
    assert encode_ilife_segments(["3", "5"]) == "AQEAAAAo"
    assert encode_ilife_segments(["0"]) == "AQEAAAAB"
    assert encode_ilife_segments(["7"]) == "AQEAAACA"
