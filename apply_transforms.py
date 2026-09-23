#!/usr/bin/env python3
import sys, os, time
os.chdir("C:/Users/noman/Desktop/live-tracker")
sys.path.insert(0, "C:/Users/noman/Desktop/live-tracker")

from clicker import Clicker

c = Clicker("Blender", shots=False)

print("\n" + "="*60)
print("APPLYING TRANSFORMATIONS VIA CLICKING")
print("="*60)

print("\n[1] Scale X = 2 (click Scale X field)...")
c.click_at(1388, 571, relative=True, desc="Scale X field")
time.sleep(0.3)
c.press("ctrl+a")  # Select all in field
time.sleep(0.1)
c.write("2")
time.sleep(0.1)
c.press("Return")
time.sleep(0.5)

print("[2] Scale Y = 2...")
c.click_at(1388, 590, relative=True, desc="Scale Y field")
time.sleep(0.3)
c.press("ctrl+a")
time.sleep(0.1)
c.write("2")
time.sleep(0.1)
c.press("Return")
time.sleep(0.5)

print("[3] Scale Z = 2...")
c.click_at(1388, 609, relative=True, desc="Scale Z field")
time.sleep(0.3)
c.press("ctrl+a")
time.sleep(0.1)
c.write("2")
time.sleep(0.1)
c.press("Return")
time.sleep(0.5)

print("\n[4] Rotation X = 45 (degrees)...")
c.click_at(1398, 484, relative=True, desc="Rotation X field")
time.sleep(0.3)
c.press("ctrl+a")
time.sleep(0.1)
c.write("45")
time.sleep(0.1)
c.press("Return")
time.sleep(0.5)

print("\n[5] Location Z = 2...")
c.click_at(1398, 459, relative=True, desc="Location Z field")
time.sleep(0.3)
c.press("ctrl+a")
time.sleep(0.1)
c.write("2")
time.sleep(0.1)
c.press("Return")
time.sleep(1)

print("\n" + "="*60)
print("✓ TRANSFORMATIONS APPLIED VIA CLICKING")
print("="*60 + "\n")
