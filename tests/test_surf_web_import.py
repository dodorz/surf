import json
import os
import subprocess
import sys
import textwrap


def test_surf_web_imports_sibling_surf_when_old_module_precedes_repo(tmp_path):
    fake_dir = tmp_path / "fake"
    fake_dir.mkdir()
    (fake_dir / "surf.py").write_text(
        "class Fetcher:\n"
        "    pass\n"
        "__version__ = '0.0.0-old'\n",
        encoding="utf-8",
    )

    repo_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
    env = os.environ.copy()
    env["PYTHONPATH"] = os.pathsep.join([str(fake_dir), repo_dir])

    code = textwrap.dedent(
        """
        import json
        import os
        import sys
        import surf_web
        surf = sys.modules["surf"]

        print(json.dumps({
            "surf_file": os.path.abspath(surf.__file__),
            "same_fetcher": surf_web.Fetcher is surf.Fetcher,
            "has_fx_renderer": hasattr(surf_web.Fetcher, "_render_fx_text_html"),
        }))
        """
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=str(tmp_path),
        env=env,
        check=True,
        text=True,
        capture_output=True,
    )

    payload = json.loads(result.stdout)
    assert payload["surf_file"] == os.path.join(repo_dir, "surf.py")
    assert payload["same_fetcher"] is True
    assert payload["has_fx_renderer"] is True
