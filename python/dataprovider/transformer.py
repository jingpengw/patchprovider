#!/usr/bin/env python
__doc__ = """

Transform classes.

Kisuk Lee <kisuklee@mit.edu>, 2017
"""

import datatools
import numpy as np
import time

from . import transform as tf
from . import utils


class Transformer(object):
    """
    A sequence of transfors.
    """

    def __init__(self):
        self._transforms = list()

    def __call__(self, sample, **kwargs):
        for tf in self._transforms:
            sample = tf(sample, **kwargs)
        return sample

    def append(self, tf):
        assert isinstance(tf, Transform)
        self._transforms.append(tf)


class Transform(object):
    """
    Transform interface.
    """

    def __call__(self, sample, **kwargs):
        raise NotImplementedError

    def extract(self, sample, key):
        """Return mask if any, or else default one (all ones)."""
        assert key in sample
        if key+'_mask' in sample:
            msk = sample[key+'_mask'].astype('float32')
        else:
            msk = np.ones(sample[key].shape, 'float32')
        return sample[key], msk


class Boundary(Transform):
    """
    Binary boundary prediction.
    """

    def __init__(self, source, target, rebalance=True):
        self.source = source
        self.target = target
        self.rebalance = rebalance

    def __call__(self, sample, **kwargs):
        bdr, msk = self.extract(sample, self.source)
        # Boundary.
        lbl, _ = tf.multiclass_expansion(bdr, ids=[0])
        # Rebalancing.
        if self.rebalance:
            msk = tf.rebalance_binary_class(lbl, msk)
        # Update sample.
        sample[self.target] = lbl
        sample[self.target+'_mask'] = msk
        return sample


class Segmentation(Transform):
    """
    Segmentation.
    """

    def __init__(self, source, target, recompute=True):
        """Initialize parameters.

        Args:
            source: Key to source data.
            target: Key to target data.
            recompute: Recompute connected components.
        """
        self.source = source
        self.target = target
        self.recompute = recompute

    def __call__(self, sample, **kwargs):
        """Affinity label processing."""
        seg, msk = self.extract(sample, self.source)

        # Recompute connected components.
        if self.recompute:
            shape = (3,) + seg.shape[-3:]
            aff = np.zeros(shape, dtype='float32')
            tf.affinitize(seg, ret=aff[0,...], dst=(0,0,1))
            tf.affinitize(seg, ret=aff[1,...], dst=(0,1,0))
            tf.affinitize(seg, ret=aff[2,...], dst=(1,0,0))
            seg = datatools.get_segmentation(aff)

        # Update sample.
        sample[self.target] = utils.check_volume(seg.astype('float32'))
        sample[self.target+'_mask'] = utils.check_volume(msk.astype('float32'))

        return sample


class Affinity(Transform):
    """
    Expand segmentation into affinity represntation.
    """

    def __init__(self, dst, source, target, crop=None, crop_size=None,
                    base_w=None, recompute=True):
        """Initialize parameters.

        Args:
            dst: List of 3-tuples, each indicating affinity distance in (z,y,x).
            source: Key to source data from which to construct affinity.
            target: Key to target data.
            crop: 3-tuple indicating crop offset.
            crop-size:
            base_w: base weight for class-rebalanced gradient weight mask.
            recompute:
        """
        self.dst = dst
        self.source = source
        self.target = target
        self.crop = crop
        self.crop_size = crop_size
        self.base_w = base_w
        self.recompute = recompute

    def __call__(self, sample, **kwargs):
        """Affinity label processing."""
        seg, msk = self.extract(sample, self.source)

        # Recompute connected components.
        if self.recompute:
            shape = (3,) + seg.shape[-3:]
            aff = np.zeros(shape, dtype='float32')
            tf.affinitize(seg, ret=aff[0,...], dst=(0,0,1))
            tf.affinitize(seg, ret=aff[1,...], dst=(0,1,0))
            tf.affinitize(seg, ret=aff[2,...], dst=(1,0,0))
            seg = datatools.get_segmentation(aff)

        # Affinitize.
        shape = (len(self.dst),) + seg.shape[-3:]
        affs = np.zeros(shape, dtype='float32')
        msks = np.zeros(shape, dtype='float32')
        for i, dst in enumerate(self.dst):
            tf.affinitize(seg, ret=affs[i,...], dst=dst)
            tf.affinitize_mask(msk, ret=msks[i,...], dst=dst)
        lbl = affs
        msk = msks

        # Rebalancing.
        if self.base_w is not None:
            for c in range(lbl.shape[0]):
                msk[c,...] = tf.rebalance_binary_class(lbl[c,...], msk=msk[c,...], base_w=self.base_w)

        # Update sample.
        sample[self.target] = lbl
        sample[self.target+'_mask'] = msk

        # Crop.
        if self.crop is not None:
            for k, v in sample.items():
                sample[k] = tf.crop(v, offset=self.crop, size=self.crop_size)

        return sample


class Semantic(Transform):
    """
    Expand semantic segmentation into multiclass represntation.
    """

    def __init__(self, ids, source, target, rebalance=True):
        """Initialize parameters.

        Args:
            ids: List of ids to expand.
            source: Key to source data from which to construct target.
            target: Key to target data.
            rebalance: Class-rebalanced gradient weight mask.
        """
        self.ids = ids
        self.source = source
        self.target = target
        self.rebalance = rebalance

    def __call__(self, sample, **kwargs):
        """Semantic label processing."""
        sem, msk = self.extract(sample, self.source)
        # Semantic class expansion.
        lbl, msk2 = tf.multiclass_expansion(sem, ids=self.ids)
        # Combine with a given mask.
        msk *= msk2
        # Rebalancing.
        if self.rebalance:
            for i, _ in enumerate(self.ids):
                msk[i,...] = tf.rebalance_binary_class(lbl[i,...], msk[i,...])
        # Update sample.
        sample[self.target] = lbl
        sample[self.target+'_mask'] = msk
        return sample


class Synapse(Transform):
    """
    Transform synapse segmentation into binary representation.
    """

    def __init__(self, source, target, rebalance=False, base_w=0.0):
        self.source = source
        self.target = target
        self.rebalance = rebalance
        self.base_w = base_w

    def __call__(self, sample, **kwargs):
        """Synapse label processing."""
        syn, msk = self.extract(sample, self.source)
        # Binarize.
        lbl = tf.binarize(syn)
        # Rebalancing.
        if self.rebalance:
            msk = tf.rebalance_binary_class(lbl, msk, base_w=self.base_w)
        # Update sample.
        sample[self.target] = lbl
        sample[self.target+'_mask'] = msk
        return sample


class ObjectInstance(Transform):
    """
    Object instance segmentation.
    """

    def __init__(self, source, target, rebalance=True):
        self.source = source
        self.target = target
        self.rebalance = rebalance

    def __call__(self, sample, **kwargs):
        seg, msk = self.extract(sample, self.source)
        # Binarize.
        object_id = kwargs['object_id'] if 'object_id' in kwargs else None
        lbl = tf.binarize_object(seg, object_id=object_id)
        # Rebalancing.
        if self.rebalance:
            msk = tf.rebalance_binary_class(lbl, msk)
        # Replace sample.
        sample[self.target] = lbl
        sample[self.target+'_mask'] = msk
        return sample


class CenterInstance(ObjectInstance):
    """
    Center object instance with mask.
    """

    def __init__(self, source, target, mask=None, rebalance=True):
        self.source = source
        self.target = target
        self.mask = mask
        self.rebalance = rebalance

    def __call__(self, sample, **kwargs):
        # Object instance mask.
        if self.mask is not None:
            seg = sample[self.source]
            z, y, x = seg.shape[-3:]
            mask = np.zeros(seg.shape, dtype='float32')
            mask[...,z//2,y//2,x//2] = 1
            sample[self.mask] = mask
        return super(CenterInstance, self).__call__(sample, **kwargs)
