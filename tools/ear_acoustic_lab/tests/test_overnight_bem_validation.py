"""Mocked unattended BEM suite: no real acoustic solver or long job."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import overnight_bem_validation as overnight


def make_project(root):
    root = Path(root)
    repo = root / "repo"
    binary = repo / "mesh2hrtf" / "NumCalc" / "bin" / "NumCalc"
    binary.parent.mkdir(parents=True)
    binary.write_text("#!/bin/sh\nexit 0\n")
    binary.chmod(0o755)
    project = root / "new_export"
    source = project / "NumCalc" / "source_1"
    source.mkdir(parents=True)
    nc = (
        "##\n## 1. Main Parameters I\n"
        "2 4 4 0 0 2 1 4 0\n##\n"
        "../../ObjectMeshes/Reference/Nodes.txt\n")
    (source / "NC.inp").write_text(nc)
    mesh = project / "ObjectMeshes" / "Reference"
    mesh.mkdir(parents=True)
    nodes = [
        (0.,0.,0.),(.001,0.,0.),(0.,.001,0.),(0.,0.,.001)
    ]
    (mesh / "Nodes.txt").write_text(
        "4\n" + "".join(
            f"{i} {x} {y} {z}\n" for i,(x,y,z) in enumerate(nodes)))
    faces = [(0,2,1),(0,1,3),(0,3,2),(1,2,3)]
    (mesh / "Elements.txt").write_text(
        "4\n" + "".join(
            f"{i} {a} {b} {c} 0 0 0\n"
            for i,(a,b,c) in enumerate(faces)))
    (project / "EvaluationGrids").mkdir()
    (project / "parameters.json").write_text(json.dumps({
        "frequencies": [100 + i*(19900./79) for i in range(80)],
        "speedOfSound": 343.,
    }))
    (project / "earlab_config.json").write_text(json.dumps({
        "mic": [0,0,0], "source": [0,120,0], "units": "mm"
    }))
    return project, repo


def synthesize_result(cwd, step):
    """Write one complete true-residual NumCalc result into sandbox."""
    cwd = Path(cwd)
    nc = (cwd / "NC.inp").read_text()
    row = nc.split("## 1. Main Parameters I",1)[1].splitlines()[1].split()
    method, order = int(row[7]), 8 if row[3]=="0" else int(row[3])
    name = {0:"Traditional BEM",1:"Single level fast multipole BEM",
            4:"Direct multilevel fast multipole BEM"}[method]
    amp = {(0,8):.12,(1,8):.1,(4,8):.2,(1,10):.11,(4,10):.13}[
        method,order]
    assert (cwd / "../../ObjectMeshes/Reference/Nodes.txt").is_file()
    assert (cwd / "../../EvaluationGrids").is_dir()
    p = cwd / "be.out" / f"be.{step}" / "pEvalGrid"
    p.parent.mkdir(parents=True)
    p.write_text(
        "Mesh2HRTF 1.3.0\n1\n1 3\n" +
        "".join(f"{10000000+i} {amp} 0\n" for i in range(3)))
    (cwd / f"NC{step}-{step}.out").write_text(
        name + "\n"
        "GMRES fallback: iterations = 4, "
        "true relative residual = 8e-9\n"
        "End time: finished\n")


class OvernightValidationTest(unittest.TestCase):
    def test_method_changes_only_expansion_and_method_field(self):
        original = (
            "## 1. Main Parameters I\n2 4 4 0 0 2 1 4 0\n"
            "##\nxyz\n")
        self.assertIn("2 4 4 0 0 2 1 0 0",
                      overnight.set_numcalc_method(original, 0, 8))
        self.assertIn("2 4 4 0 0 2 1 1 0",
                      overnight.set_numcalc_method(original, 1, 8))
        self.assertIn("2 4 4 10 0 2 1 4 0",
                      overnight.set_numcalc_method(original, 4, 10))
        self.assertEqual(
            overnight.set_numcalc_method(original, 4, 8), original)
        with self.assertRaises(ValueError):
            overnight.set_numcalc_method(original, 4, 11)
        with self.assertRaises(ValueError):
            overnight.set_numcalc_method(
                original.replace(" 4 0\n", " 4 4\n"), 4, 8)

    def test_two_steps_are_bounded_separate_and_resume_without_overwriting(self):
        with tempfile.TemporaryDirectory() as d:
            project, repo = make_project(d)
            orig = overnight._snapshot(project)
            calls = []
            def fake_run(command,cwd,log_path,timeout_s,**kw):
                step = int(command[command.index("-istart")+1])
                assert command[command.index("-iend")+1] == str(step)
                self.assertLessEqual(timeout_s, 900)
                calls.append((step, Path(cwd)))
                Path(log_path).write_text("mock process\n")
                synthesize_result(cwd, step)
                return {"return_code":0,"stop_reason":None,
                        "seconds_elapsed":1.,"max_observed_process_rss_gib":.1}
            script_file = repo / "tools" / "ear_acoustic_lab" / (
                "overnight_bem_validation.py")
            script_file.parent.mkdir(parents=True)
            with patch.object(overnight,"__file__",str(script_file)), \
                 patch.object(overnight,"_run_with_guards",side_effect=fake_run):
                results = overnight.run(
                    project, steps=(13,14), per_case_timeout=900,
                    max_hours=1, reuse=False)
            self.assertEqual(len(calls), 10)
            self.assertEqual(len(set(calls)), 10)
            self.assertTrue(all(k in results for k in [
                "13:sl8","13:ml8","13:sl10","13:ml10","13:dense",
                "14:sl8","14:ml8","14:sl10","14:ml10","14:dense"]))
            self.assertTrue(all(v["status"] == "converged"
                                for v in results.values()))
            self.assertEqual(overnight._snapshot(project),orig)
            root = project / "analysis" / "overnight_bem_validation_steps_13_14"
            self.assertIn("minus dense",
                          (root / "overnight_report.txt").read_text())
            self.assertFalse((project / "NumCalc" /
                              "source_1" / "be.out").exists())
            with patch.object(overnight,"__file__",str(script_file)), \
                 patch.object(overnight,"_run_with_guards",
                              side_effect=AssertionError("Cannot rerun completed!")):
                again = overnight.run(
                    project, steps=(13,14), per_case_timeout=900,
                    max_hours=1, reuse=False)
            self.assertEqual(results,again)

    def test_uses_verified_existing_step13_without_recalculating(self):
        with tempfile.TemporaryDirectory() as d:
            project, repo = make_project(d)
            original = (project/"NumCalc/source_1/NC.inp").read_text()
            folder = project / "analysis" / "fmm_new_mesh_step13_baseline"
            folder.mkdir(parents=True)
            inputs = overnight._snapshot(project)
            (folder/"comparison.json").write_text(json.dumps({
                "step":13,
                "frequency_hz":100+12*(19900/79),
                "original_sha256": {
                    k:inputs[k] for k in ("NC.inp","Nodes.txt","Elements.txt")},
            }))
            for label, method in [("single_level",1),("multilevel",4)]:
                source = folder / label / "NumCalc" / "source_1"
                source.mkdir(parents=True)
                (source / "NC.inp").write_text(
                    overnight.set_numcalc_method(original,method,8))
                # This helper only tests output parsing, not relative links.
                nc = (source/"NC.inp").read_text()
                row = nc.split("## 1. Main Parameters I",1)[1].splitlines()[1].split()
                m = row[7]
                title = ("Single level fast multipole BEM" if m=="1"
                         else "Direct multilevel fast multipole BEM")
                p=source/"be.out/be.13/pEvalGrid"
                p.parent.mkdir(parents=True)
                p.write_text("Mesh2HRTF 1.3.0\n1\n1 3\n"+
                    "".join(f"{10000000+i} .1 0\n" for i in range(3)))
                (source/"NC13-13.out").write_text(
                    title+"\nGMRES fallback: iterations = 2, "
                    "true relative residual = 8e-9\nEnd time: done\n")
            script = repo/"tools/ear_acoustic_lab/overnight_bem_validation.py"
            script.parent.mkdir(parents=True)
            calls=[]
            def runner(command,cwd,log_path,timeout_s,**kw):
                calls.append(Path(cwd).parent.parent.parent.name)
                Path(log_path).write_text("")
                synthesize_result(cwd,13)
                return {"return_code":0,"stop_reason":None,
                        "seconds_elapsed":1.,
                        "max_observed_process_rss_gib":.1}
            with patch.object(overnight,"__file__",str(script)), \
                 patch.object(overnight,"_run_with_guards",side_effect=runner):
                results=overnight.run(
                    project,steps=(13,),per_case_timeout=60,max_hours=1)
            self.assertEqual(len(calls),3)
            self.assertEqual(results["13:sl8"]["source"],
                             "verified_repaired_mesh_baseline")
            self.assertEqual(results["13:ml8"]["source"],
                             "verified_repaired_mesh_baseline")

    def test_failed_variant_is_preserved_and_next_variant_runs(self):
        with tempfile.TemporaryDirectory() as d:
            project, repo=make_project(d)
            script=repo/"tools/ear_acoustic_lab/overnight_bem_validation.py"
            script.parent.mkdir(parents=True)
            calls=[]
            def runner(command,cwd,log_path,timeout_s,**kw):
                calls.append(Path(cwd))
                Path(log_path).write_text("killed by guard\n")
                if len(calls)==1:
                    return {"return_code":-15,
                            "stop_reason":"Physical RAM guard",
                            "seconds_elapsed":2.,
                            "max_observed_process_rss_gib":2.}
                synthesize_result(cwd,13)
                return {"return_code":0,"stop_reason":None,
                        "seconds_elapsed":1.,
                        "max_observed_process_rss_gib":.1}
            with patch.object(overnight,"__file__",str(script)), \
                 patch.object(overnight,"_run_with_guards",side_effect=runner):
                results=overnight.run(
                    project,steps=(13,),per_case_timeout=60,max_hours=1,
                    reuse=False)
            self.assertEqual(len(calls),5)
            self.assertEqual(results["13:sl8"]["status"],"failed")
            self.assertEqual(results["13:ml8"]["status"],"converged")
            self.assertIn("RAM guard",results["13:sl8"]["reason"])


if __name__ == "__main__":
    unittest.main()
