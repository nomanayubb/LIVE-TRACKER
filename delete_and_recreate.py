#!/usr/bin/env python3
import sys, os, time, json
os.chdir("C:/Users/noman/Desktop/live-tracker")
sys.path.insert(0, "C:/Users/noman/Desktop/live-tracker")

from clicker import Clicker

c = Clicker("Blender", shots=False)

print("\n" + "="*60)
print("DELETING OLD CUBE AND CREATING NEW ONE BY CLICKING ONLY")
print("="*60)

print("\n[1] Select all objects and delete...")
c.press("a")  # Select all
time.sleep(0.2)
c.press("x")  # Delete
time.sleep(0.5)

print("[2] Focus Blender...")
c.focus()
time.sleep(0.5)

print("\n[3] Reading live tracker data to find ADD button...")
with open(".live_screen_state.json", "r") as f:
    state = json.load(f)
    ocr_boxes = state.get("ocr_boxes", [])
    print(f"    Found {len(ocr_boxes)} OCR boxes")

    # Find "Add" button
    add_box = None
    for box in ocr_boxes:
        if "Add" in box.get("text", ""):
            add_box = box
            print(f"    ✓ Add button found at: x={box['x']}, y={box['y']}, w={box['w']}, h={box['h']}")
            break

    if add_box:
        # Calculate center with offset correction
        center_x = add_box['x'] + add_box['w'] / 2
        center_y = add_box['y'] + add_box['h'] / 2 - 5  # Adjusted for offset
        print(f"    Center adjusted: ({center_x}, {center_y})")
    else:
        center_x, center_y = 330, 108
        print(f"    Using fallback: ({center_x}, {center_y})")

print(f"\n[4] Click ADD at ({center_x}, {center_y})...")
c.click_at(int(center_x), int(center_y), relative=True, desc="Add menu")
time.sleep(1)

print("[5] Click MESH...")
c.click_at(267, 142, relative=True, desc="Mesh")
time.sleep(1)

print("[6] Click CUBE...")
c.click_at(276, 170, relative=True, desc="Cube")
time.sleep(1)

print("\n[7] Verify via live frame...")
print("    → Check .live_frame.jpg to confirm cube exists")
print("    → Use tracker visual data, not blind execution!")

print("\n" + "="*60)
print("✓ CUBE CREATED BY CLICKING (using optimized tool)")
print("="*60 + "\n")
