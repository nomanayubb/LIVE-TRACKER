#!/usr/bin/env python3
"""
Create a cube in Blender using bpy API, then verify via live tracker.
This is the CORRECT approach — use the app's own API, not GUI automation.
"""
import bpy
import math

print("\n" + "="*60)
print("Creating cube via bpy API (correct approach)")
print("="*60)

# Clear any existing selection
bpy.ops.object.select_all(action='SELECT')
bpy.ops.object.delete(use_global=False)

# Add cube
print("[1] Adding cube...")
bpy.ops.mesh.primitive_cube_add()
cube = bpy.context.active_object
print(f"    ✓ Cube created: {cube.name}")

# Transform
print("[2] Applying transformations...")
print(f"    - Scale: 2x (from 1x)")
cube.scale = (2, 2, 2)

print(f"    - Rotate X axis: 45°")
cube.rotation_euler[0] = math.radians(45)

print(f"    - Move Z axis: +2 (from 0)")
cube.location[2] = 2

print(f"\n✓ Final state:")
print(f"  Scale: {cube.scale}")
print(f"  Rotation (radians): {cube.rotation_euler}")
print(f"  Location: {cube.location}")
print(f"\nVerify in Blender and check .live_frame.jpg")
print("="*60 + "\n")
