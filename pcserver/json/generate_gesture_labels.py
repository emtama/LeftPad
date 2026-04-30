import json

gesture_finger_variations = ["single_finger", "two_finger", "three_finger"]
gesture_finger_variations_labels = ["一本指", "二本指", "三本指"]

tap_variations = ["tap", "double_tap", "triple_tap", "long_press"]
tap_variations_labels = ["タップ", "ダブルタップ", "トリプルタップ", "長押し"]
tap_region_variations = ["top", "bottom", "left", "right"]
tap_region_variations_labels = ["上部", "下部", "左部", "右部"]

swipe_direction_variations = ["up", "down", "left", "right"]
swipe_direction_variations_labels = ["上", "下", "左", "右"]

GESTURE_KEYS = [
    "pinch_in", 
    "pinch_out",
] + [
    f"{finger}_{variation}_{region}" for finger in gesture_finger_variations for variation in tap_variations for region in tap_region_variations
] + [
    f"{finger}_swipe_{direction}" for finger in gesture_finger_variations for direction in swipe_direction_variations
]

GESTURE_LABELS_JP = {
    "pinch_in": "ピンチイン", 
    "pinch_out": "ピンチアウト"
} | {
    f"{finger}_{variation}_{region}": f"{finger_label}{tap_label} ({region_label})"
    for finger, finger_label in zip(gesture_finger_variations, gesture_finger_variations_labels)
    for variation, tap_label in zip(tap_variations, tap_variations_labels)
    for region, region_label in zip(tap_region_variations, tap_region_variations_labels)
} | {
    f"{finger}_swipe_{direction}": f"{finger_label}スワイプ ({direction_label})"
    for finger, finger_label in zip(gesture_finger_variations, gesture_finger_variations_labels)
    for direction, direction_label in zip(swipe_direction_variations, swipe_direction_variations_labels)
}

with open("gesture_labels.json", "w", encoding="utf-8") as f:
    json.dump(GESTURE_LABELS_JP, f, ensure_ascii=False, indent=2)