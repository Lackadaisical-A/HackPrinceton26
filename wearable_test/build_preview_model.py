# /// script
# requires-python = ">=3.12,<3.13"
# dependencies = ["trimesh>=4.6,<6", "fast-simplification>=0.1.10,<0.3", "pybullet==3.2.7"]
# ///
"""Build reduced visual meshes; preserve original URDF joints and source assets."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import time
import xml.etree.ElementTree as ET

SOURCE = Path(__file__).resolve().parents[1]/"so101_sim/model"
OUTPUT = Path(__file__).resolve().parent/"model_preview"


def cluster_vertices(mesh,cell=.001):
    """Merge sub-millimetre CAD detail before quadric reduction (STL units: m)."""
    import numpy as np
    import trimesh
    _,inverse = np.unique(np.rint(mesh.vertices/cell).astype(np.int64),axis=0,return_inverse=True)
    counts = np.bincount(inverse)
    vertices = np.zeros((len(counts),3))
    np.add.at(vertices,inverse,mesh.vertices)
    vertices /= counts[:,None]
    faces = inverse[mesh.faces]
    keep = (faces[:,0]!=faces[:,1]) & (faces[:,0]!=faces[:,2]) & (faces[:,1]!=faces[:,2])
    faces = faces[keep]
    _,unique = np.unique(np.sort(faces,axis=1),axis=0,return_index=True)
    return trimesh.Trimesh(vertices=vertices,faces=faces[unique],process=True)


def build():
    import numpy as np
    import trimesh
    tree = ET.parse(SOURCE/"so101_new_calib.urdf")
    (OUTPUT/"assets").mkdir(parents=True,exist_ok=True)
    meshes = {}
    for element in tree.findall(".//visual/geometry/mesh"):
        relative = element.get("filename")
        if relative in meshes: continue
        source = SOURCE/relative
        mesh = trimesh.load_mesh(source,process=True)
        clustered = cluster_vertices(mesh)
        simplified,error = mesh,0.
        for count in (2000,4000,8000,len(clustered.faces)):
            candidate = clustered.simplify_quadric_decimation(face_count=count,aggression=7) if len(clustered.faces)>count else clustered
            candidate_error = float(np.max(np.abs(candidate.bounds-mesh.bounds)))
            if candidate_error <= .02*float(np.max(mesh.extents)):
                simplified,error = candidate,candidate_error
                break
        simplified.export(OUTPUT/relative)
        meshes[relative] = dict(source_faces=len(mesh.faces),display_faces=len(simplified.faces),
                                max_bounds_error=error,source_sha256=hashlib.sha256(source.read_bytes()).hexdigest())
    # Display only: no simulation dynamics or collisions. All joint origins,
    # visual transforms, material colors, axes, and limits remain unchanged.
    for link in tree.findall("link"):
        for collision in link.findall("collision"): link.remove(collision)
    tree.write(OUTPUT/"so101_preview.urdf",encoding="utf-8",xml_declaration=True)
    shutil.copyfile(SOURCE/"LICENSE",OUTPUT/"LICENSE")
    manifest = dict(source="so101_sim/model/so101_new_calib.urdf",
                    source_sha256=hashlib.sha256((SOURCE/"so101_new_calib.urdf").read_bytes()).hexdigest(),
                    target_faces_per_mesh=2000,vertex_cluster_m=.001,meshes=meshes)
    (OUTPUT/"manifest.json").write_text(json.dumps(manifest,indent=2)+"\n",encoding="utf-8")
    before,after = (sum(item[key] for item in meshes.values()) for key in ("source_faces","display_faces"))
    print(f"Display meshes: {before:,} -> {after:,} triangles ({after/before:.1%}); source model preserved.")


def benchmark():
    import pybullet as p
    client = p.connect(p.DIRECT)
    try:
        for path in (SOURCE/"so101_new_calib.urdf",OUTPUT/"so101_preview.urdf"):
            p.resetSimulation(physicsClientId=client)
            p.loadURDF(str(path),useFixedBase=True,physicsClientId=client)
            view = p.computeViewMatrixFromYawPitchRoll([.1,0,.12],.85,25,-24,0,2)
            projection = p.computeProjectionMatrixFOV(45,1.6,.01,5)
            start = time.perf_counter()
            for _ in range(10):
                p.getCameraImage(640,400,viewMatrix=view,projectionMatrix=projection,
                                 renderer=p.ER_TINY_RENDERER,physicsClientId=client)
            elapsed = time.perf_counter()-start
            print(f"{path.name}: {10/elapsed:.1f} FPS, {elapsed*100:.1f} ms/frame at 640x400")
    finally: p.disconnect(client)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--benchmark",action="store_true")
    args = parser.parse_args()
    build()
    if args.benchmark: benchmark()
