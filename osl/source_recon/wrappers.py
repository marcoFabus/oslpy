"""Wrappers for source reconstruction.

This module contains the functions callable using a 'source_recon'
section of a config.

All functions in this module accept the following arguments for
consistency:

    func(src_dir, subject, preproc_file, smri_file, *userargs, logger)

Custom functions (i.e. functions passed via the extra_funcs argument)
must also conform to this.
"""

# Authors: Chetan Gohil <chetan.gohil@psych.ox.ac.uk>


import logging
import os.path as op
from pathlib import Path
from glob import glob

import numpy as np

from . import rhino, beamforming, parcellation, sign_flipping
from ..utils import validate_outdir


logger = logging.getLogger(__name__)


# ------------------------------------------------------------------
# RHINO wrappers


def extract_fiducials_from_fif(
    src_dir,
    subject,
    preproc_file,
    smri_file,
    logger,
    **userargs,
):
    """Wrapper function to extract fiducials/headshape points.

    Parameters
    ----------
    src_dir : str
        Path to where to output the source reconstruction files.
    subject : str
        Subject name/id.
    preproc_file : str
        Path to the preprocessed fif file.
    smri_file : str
        Path to the T1 weighted structural MRI file to use in source reconstruction.
    logger : logging.getLogger
        Logger.
    userargs : keyword arguments
        Keyword arguments to pass to
        osl.source_recon.rhino.extract_polhemus_from_info.
    """
    filenames = rhino.get_coreg_filenames(src_dir, subject)

    logger.info("Setting up polhemus files")
    rhino.extract_polhemus_from_info(
        fif_file=preproc_file,
        headshape_outfile=filenames["polhemus_headshape_file"],
        nasion_outfile=filenames["polhemus_nasion_file"],
        rpa_outfile=filenames["polhemus_rpa_file"],
        lpa_outfile=filenames["polhemus_lpa_file"],
        **userargs,
    )
    logger.info(f"saved: {filenames['polhemus_headshape_file']}")
    logger.info(f"saved: {filenames['polhemus_nasion_file']}")
    logger.info(f"saved: {filenames['polhemus_rpa_file']}")
    logger.info(f"saved: {filenames['polhemus_lpa_file']}")


def coregister(
    src_dir,
    subject,
    preproc_file,
    smri_file,
    logger,
    include_nose,
    use_nose,
    use_headshape,
    model,
    eeg=False,
):
    """Wrapper for coregistration.

    Parameters
    ----------
    src_dir : str
        Path to where to output the source reconstruction files.
    subject : str
        Subject name/id.
    preproc_file : str
        Path to the preprocessed fif file.
    smri_file : str
        Path to the T1 weighted structural MRI file to use in source reconstruction.
    logger : logging.getLogger
        Logger.
    include_nose : bool
        Should we include the nose when we're extracting the surfaces?
    use_nose : bool
        Should we use the nose in the coregistration?
    use_headshape : bool
        Should we use the headshape points in the coregistration?
    model : str
        Forward model to use.
    eeg : bool
        Are we using EEG channels in the source reconstruction?
    """
    # Compute surface
    rhino.compute_surfaces(
        smri_file=smri_file,
        subjects_dir=src_dir,
        subject=subject,
        include_nose=include_nose,
        logger=logger,
    )

    # Run coregistration
    rhino.coreg(
        fif_file=preproc_file,
        subjects_dir=src_dir,
        subject=subject,
        use_headshape=use_headshape,
        use_nose=use_nose,
        logger=logger,
    )

    # Compute forward model
    rhino.forward_model(
        subjects_dir=src_dir,
        subject=subject,
        model=model,
        eeg=eeg,
        logger=logger,
    )


# ------------------------------------------------------------------
# Beamforming and parcellation wrappers


def beamform_and_parcellate(
    src_dir,
    subject,
    preproc_file,
    smri_file,
    logger,
    chantypes,
    rank,
    freq_range,
    parcellation_file,
    method,
    orthogonalisation,
):
    """Wrapper function for beamforming and parcellation.

    Parameters
    ----------
    src_dir : str
        Path to where to output the source reconstruction files.
    subject : str
        Subject name/id.
    preproc_file : str
        Path to the preprocessed fif file.
    smri_file : str
        Path to the T1 weighted structural MRI file to use in source reconstruction.
    logger : logging.getLogger
        Logger.
    chantypes : list of str
        Channel types to use in beamforming.
    rank : dict
        Keys should be the channel types and the value should be the rank to use.
    freq_range : list
        Lower and upper band to bandpass filter before beamforming. If None,
        no filtering is done.
    parcellation_file : str
        Path to the parcellation file to use.
    method : str
        Method to use in the parcellation.
    orthogonalisation : bool
        Should we do orthogonalisation?
    """
    from ..preprocessing import import_data

    # Load preprocessed data
    preproc_data = import_data(preproc_file)
    preproc_data.pick(chantypes)

    if freq_range is not None:
        # Bandpass filter
        logger.info("bandpass filtering: {}-{} Hz".format(freq_range[0], freq_range[1]))
        preproc_data = preproc_data.filter(
            l_freq=freq_range[0],
            h_freq=freq_range[1],
            method="iir",
            iir_params={"order": 5, "ftype": "butter"},
        )

    # Validation
    if isinstance(chantypes, str):
        chantypes = [chantypes]

    # Create beamforming filters
    logger.info("beamforming.make_lcmv")
    logger.info(f"chantypes: {chantypes}")
    logger.info(f"rank: {rank}")
    filters = beamforming.make_lcmv(
        subjects_dir=src_dir,
        subject=subject,
        data=preproc_data,
        chantypes=chantypes,
        weight_norm="nai",
        rank=rank,
        logger=logger,
        save_figs=True,
    )

    # Apply beamforming
    logger.info("beamforming.apply_lcmv_raw")
    src_data = beamforming.apply_lcmv_raw(preproc_data, filters)
    src_ts_mni, _, src_coords_mni, _ = beamforming.transform_recon_timeseries(
        subjects_dir=src_dir,
        subject=subject,
        recon_timeseries=src_data.data,
    )

    # Parcellation
    logger.info("parcellation")
    logger.info(parcellation_file)
    p = parcellation.Parcellation(parcellation_file)
    p.parcellate(
        voxel_timeseries=src_ts_mni,
        voxel_coords=src_coords_mni,
        method=method,
        logger=logger,
    )
    parcel_ts = p.parcel_timeseries["data"]

    # Orthogonalisation
    if orthogonalisation not in [None, "symmetric"]:
        raise NotImplementedError(orthogonalisation)

    if orthogonalisation == "symmetric":
        logger.info(f"{orthogonalisation} orthogonalisation")
        parcel_ts = parcellation.symmetric_orthogonalise(
            parcel_ts, maintain_magnitudes=True
        )

    # Save parcellated data
    parc_data_file = src_dir / subject / "rhino/parc.npy"
    logger.info(f"saving {parc_data_file}")
    np.save(parc_data_file, parcel_ts.T)


# ------------------------------------------------------------------
# Sign flipping wrappers


def find_template_subject(src_dir, subjects, n_embeddings=1, standardize=True):
    """Function to find a good subject to align other subjects to in the sign flipping.

    Note, this function expects parcellated data to exist in the following location:
    src_dir/*/rhino/parc.npy, the * here represents subject directories.

    Parameters
    ----------
    src_dir : str
        Path to where to output the source reconstruction files.
    subjects : str
        Subjects to include.
    n_embeddings : int
        Number of time-delay embeddings that we will use (if we are doing any).
    standardize : bool
        Should we standardize (z-transform) the data before sign flipping?

    Returns
    -------
    template : str
        Template subject.
    """
    print("Finding template subject:")

    # Get the parcellated data files
    parc_files = []
    for subject in subjects:
        parc_file = op.join(src_dir, subject, "rhino", "parc.npy")
        if Path(parc_file).exists():
            parc_files.append(parc_file)
        else:
            print(f"Warning: {parc_file} not found")

    # Validation
    n_parc_files = len(parc_files)
    if n_parc_files < 2:
        raise ValueError(
            "two or more parcellated data files are needed to perform "
            + f"sign flipping, got {n_parc_files}"
        )

    # Calculate the covariance matrix of each subject
    covs = sign_flipping.load_covariances(parc_files, n_embeddings, standardize)

    # Find a subject to use as a template
    template_index = sign_flipping.find_template_subject(covs, n_embeddings)
    template_subject = parc_files[template_index].split("/")[-3]
    print("Template for sign flipping:", template_subject)

    return template_subject


def fix_sign_ambiguity(
    src_dir,
    subject,
    preproc_file,
    smri_file,
    logger,
    template,
    n_embeddings,
    standardize,
    n_init,
    n_iter,
    max_flips,
):
    """Wrapper function for fixing the dipole sign ambiguity.

    Parameters
    ----------
    src_dir : str
        Path to where to output the source reconstruction files.
    subject : str
        Subject name/id.
    preproc_file : str
        Path to the preprocessed fif file.
    smri_file : str
        Path to the T1 weighted structural MRI file to use in source reconstruction.
    logger : logging.getLogger
        Logger.
    template : str
        Template subject.
    n_embeddings : int
        Number of time-delay embeddings that we will use (if we are doing any).
    standardize : bool
        Should we standardize (z-transform) the data before sign flipping?
    n_init : int
        Number of initializations.
    n_iter : int
        Number of sign flipping iterations per subject to perform.
    max_flips : int
        Maximum number of channels to flip in an iteration.
    """
    logger.info("fix_sign_ambiguity")
    logger.info(f"using template: {template}")

    # Get path to the parcellated data file for this subject and the template
    parc_files = []
    for sub in [subject, template]:
        parc_file = op.join(src_dir, sub, "rhino", "parc.npy")
        if not Path(parc_file).exists():
            raise ValueError(f"{parc_file} not found")
        parc_files.append(parc_file)

    # Calculate the covariance of this subject and the template
    [cov, template_cov] = sign_flipping.load_covariances(
        parc_files, n_embeddings, standardize, use_tqdm=False
    )

    # Find the channels to flip
    flips, metrics = sign_flipping.find_flips(
        cov, template_cov, n_embeddings, n_init, n_iter, max_flips, logger
    )

    # Apply flips to the parcellated data
    sign_flipping.apply_flips(src_dir, subject, flips, logger)

    # Plot a summary figure describing the sign flipping solution
    sign_flipping.plot_sign_flipping(
        src_dir, subject, cov, template_cov, n_embeddings, flips, metrics
    )
