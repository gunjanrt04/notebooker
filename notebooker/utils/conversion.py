import os
import uuid
from typing import Any, AnyStr, Dict, Optional

import git
import jupytext
import nbformat
import pkg_resources
from nbconvert import HTMLExporter, PDFExporter
from nbconvert.exporters.exporter import ResourcesDict
from traitlets.config import Config

from notebooker.constants import NOTEBOOKER_DISABLE_GIT, TEMPLATE_DIR_SEPARATOR, kernel_spec, python_template_dir
from notebooker.utils.caching import get_cache, set_cache
from notebooker.utils.filesystem import mkdir_p
from notebooker.utils.notebook_execution import logger


def get_resources_dir(job_id):
    return "{}/resources".format(job_id)


def ipython_to_html(ipynb_path: str, job_id: str) -> (nbformat.NotebookNode, Dict[str, Any]):
    c = Config()
    c.HTMLExporter.preprocessors = ["nbconvert.preprocessors.ExtractOutputPreprocessor"]
    c.HTMLExporter.template_file = pkg_resources.resource_filename(
        __name__, "../nbtemplates/notebooker_html_output.tpl"
    )
    html_exporter_with_figs = HTMLExporter(config=c)

    with open(ipynb_path, "r") as nb_file:
        nb = nbformat.reads(nb_file.read(), as_version=nbformat.v4.nbformat)
    resources_dir = get_resources_dir(job_id)
    html, resources = html_exporter_with_figs.from_notebook_node(nb, resources={"output_files_dir": resources_dir})
    return html, resources


def ipython_to_pdf(raw_executed_ipynb: str, report_title: str) -> AnyStr:
    pdf_exporter = PDFExporter(Config())
    resources = ResourcesDict()
    resources["metadata"] = ResourcesDict()
    resources["metadata"]["name"] = report_title
    pdf, _ = pdf_exporter.from_notebook_node(
        nbformat.reads(raw_executed_ipynb, as_version=nbformat.v4.nbformat), resources=resources
    )
    return pdf


def _output_ipynb_name(report_name: str) -> str:
    return "{}.ipynb".format(convert_report_path_into_name(report_name))


def _git_pull_templates():
    repo = git.repo.Repo(os.environ["PY_TEMPLATE_DIR"])
    repo.git.pull("origin", "master")
    return repo.commit("HEAD").hexsha


def _python_template(report_path: AnyStr) -> AnyStr:
    file_name = "{}.py".format(report_path)
    return os.path.join(python_template_dir(), file_name)


def _ipynb_output_path(template_base_dir: AnyStr, report_path: AnyStr, git_hex: AnyStr) -> AnyStr:
    file_name = _output_ipynb_name(report_path)
    return os.path.join(template_base_dir, git_hex, file_name)


def _get_python_template_path(report_path: str, warn_on_local: bool) -> str:
    if python_template_dir():
        return _python_template(report_path)
    else:
        if warn_on_local:
            logger.warning(
                "Loading from notebooker default templates. This is only expected if you are running locally."
            )
        return pkg_resources.resource_filename(__name__, "../notebook_templates_example/{}.py".format(report_path))


def _get_output_path_hex() -> str:
    if python_template_dir() and not NOTEBOOKER_DISABLE_GIT:
        logger.info("Pulling latest notebook templates from git.")
        try:
            latest_sha = _git_pull_templates()
            if get_cache("latest_sha") != latest_sha:
                logger.info("Change detected in notebook template master!")
                set_cache("latest_sha", latest_sha)
            logger.info("Git pull done.")
        except Exception as e:
            logger.exception(e)
        return get_cache("latest_sha") or "OLD"
    else:
        return str(uuid.uuid4())


def convert_report_name_into_path(report_name: str) -> str:
    """ This reverses convert_report_path_into_name() so that we can find the templates within notebooker_templates/ """
    return report_name.replace(TEMPLATE_DIR_SEPARATOR, os.path.sep)


def convert_report_path_into_name(report_path: str) -> str:
    """ We remove the os.sep here so that we can have a flat hierarchy of output ipynbs. """
    return report_path.replace(os.path.sep, TEMPLATE_DIR_SEPARATOR)


def generate_ipynb_from_py(template_base_dir: str, report_name: str, warn_on_local: Optional[bool] = True) -> str:
    """
    This method EITHER:
    Pulls the latest version of the notebook templates from git, and regenerates templates if there is a new HEAD
    OR: finds the local template from the template repository using a relative path

    In both cases, this method converts the .py file into an .ipynb file which can be executed by papermill.

    :param template_base_dir: The directory in which notebook templates reside.
    :param report_name: The name of the report which we are running.
    :param warn_on_local: Whether to warn when we are searching for notebooks in the notebooker repo itself.

    :return: The filepath of the .ipynb which we have just converted.
    """
    report_path = convert_report_name_into_path(report_name)
    python_template_path = _get_python_template_path(report_path, warn_on_local)
    output_template_path = _ipynb_output_path(template_base_dir, report_path, _get_output_path_hex())

    try:
        with open(output_template_path, "r") as f:
            if f.read():
                print("Loading ipynb from cached location: %s", output_template_path)
                return output_template_path
    except IOError:
        pass

    # "touch" the output file
    print("Creating ipynb at: %s", output_template_path)
    mkdir_p(os.path.dirname(output_template_path))
    with open(output_template_path, "w") as f:
        os.utime(output_template_path, None)

    jupytext_nb = jupytext.read(python_template_path)
    jupytext_nb["metadata"]["kernelspec"] = kernel_spec()  # Override the kernel spec since we want to run it..
    jupytext.write(jupytext_nb, output_template_path)
    return output_template_path


def generate_py_from_ipynb(ipynb_path, output_dir="."):
    if not ipynb_path.endswith(".ipynb"):
        logger.error("Did not expect file extension. Expected .ipynb, got %s", os.path.splitext(ipynb_path)[1])
        return None
    mkdir_p(output_dir)
    filename_no_extension = os.path.basename(os.path.splitext(ipynb_path)[0])
    output_path = os.path.join(output_dir, filename_no_extension + ".py")
    ipynb = jupytext.read(ipynb_path)
    jupytext.write(ipynb, output_path)
    logger.info("Successfully converted %s -> %s", ipynb_path, output_path)
    return output_path
