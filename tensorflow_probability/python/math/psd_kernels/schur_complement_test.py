# Copyright 2018 The TensorFlow Probability Authors.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
# ============================================================================
"""Tests for SchurComplement."""


import functools
import itertools

from absl.testing import parameterized
import numpy as np
import tensorflow.compat.v2 as tf

import tensorflow_probability as tfp

from tensorflow_probability.python.distributions import cholesky_util
from tensorflow_probability.python.internal import samplers
from tensorflow_probability.python.internal import test_util


tfpk = tfp.math.psd_kernels


# A shape broadcasting fn
def broadcast_shapes(*shapes):
  def _broadcast_ab(a, b):
    if a == b or a == 1: return b
    if b == 1: return a
    raise ValueError("Can't broadcast {} with {}".format(a, b))
  def _broadcast_2(s1, s2):
    init_s1 = list(s1)
    init_s2 = list(s2)
    if len(s1) > len(s2):
      return _broadcast_2(s2, s1)
    # Now len(s1) <= len(s2)
    s1 = [1] * (len(s2) - len(s1)) + list(s1)
    try:
      return [_broadcast_ab(a, b) for a, b in zip(s1, s2)]
    except ValueError:
      raise ValueError(
          "Couldn't broadcast shapes {} and {}".format(init_s1, init_s2))
  return functools.reduce(_broadcast_2, shapes)


@test_util.test_all_tf_execution_regimes
class SchurComplementTest(test_util.TestCase):

  def testMismatchedFloatTypesAreBad(self):
    base_kernel = tfpk.ExponentiatedQuadratic(
        np.float64(5.), np.float64(.2))

    # Should be OK
    tfpk.SchurComplement(
        base_kernel=base_kernel,  # float64
        fixed_inputs=np.random.uniform(-1., 1., [2, 1]))

    with self.assertRaises(TypeError):
      float32_inputs = np.random.uniform(
          -1., 1., [2, 1]).astype(np.float32)

      tfpk.SchurComplement(
          base_kernel=base_kernel, fixed_inputs=float32_inputs)

  @parameterized.parameters(
      {'feature_ndims': 1, 'dims': 3},
      {'feature_ndims': 1, 'dims': 4},
      {'feature_ndims': 2, 'dims': 2},
      {'feature_ndims': 2, 'dims': 3},
      {'feature_ndims': 3, 'dims': 2},
      {'feature_ndims': 3, 'dims': 3})
  def testValuesAreCorrect(self, feature_ndims, dims):
    np.random.seed(42)
    num_obs = 5
    num_x = 3
    num_y = 3

    shape = [dims] * feature_ndims

    base_kernel = tfpk.ExponentiatedQuadratic(
        np.float64(5.), np.float64(.2), feature_ndims=feature_ndims)

    fixed_inputs = np.random.uniform(-1., 1., size=[num_obs] + shape)

    k = tfpk.SchurComplement(
        base_kernel=base_kernel, fixed_inputs=fixed_inputs)

    k_obs = self.evaluate(base_kernel.matrix(fixed_inputs, fixed_inputs))

    k_obs_chol_linop = tf.linalg.LinearOperatorLowerTriangular(
        tf.linalg.cholesky(k_obs))
    for _ in range(5):
      x = np.random.uniform(-1, 1, size=[num_x] + shape)
      y = np.random.uniform(-1, 1, size=[num_y] + shape)

      k_x_y = self.evaluate(base_kernel.apply(x, y))
      k_x_obs = self.evaluate(base_kernel.matrix(x, fixed_inputs))
      k_obs_y = self.evaluate(base_kernel.matrix(y, fixed_inputs))

      k_x_obs = np.expand_dims(k_x_obs, -2)
      k_obs_y = np.expand_dims(k_obs_y, -1)

      k_obs_inv_k_obs_y = self.evaluate(
          k_obs_chol_linop.solve(
              k_obs_chol_linop.solve(k_obs_y),
              adjoint=True))

      cov_dec = np.einsum('ijk,ikl->ijl', k_x_obs, k_obs_inv_k_obs_y)
      cov_dec = cov_dec[..., 0, 0]  # np.squeeze didn't like list of axes
      expected = k_x_y - cov_dec
      self.assertAllClose(expected, self.evaluate(k.apply(x, y)))

  def testApplyShapesAreCorrect(self):
    for example_ndims in range(0, 4):
      # An integer generator.
      ints = itertools.count(start=2, step=1)
      feature_shape = [next(ints), next(ints)]

      x_batch_shape = [next(ints)]
      z_batch_shape = [next(ints), 1]
      num_x = [next(ints) for _ in range(example_ndims)]
      num_z = [next(ints)]

      x_shape = x_batch_shape + num_x + feature_shape
      z_shape = z_batch_shape + num_z + feature_shape

      x = np.ones(x_shape, np.float64)
      z = np.random.uniform(-1., 1., size=z_shape)

      base_kernel = tfpk.ExponentiatedQuadratic(
          amplitude=np.ones([next(ints), 1, 1], np.float64),
          feature_ndims=len(feature_shape))

      k = tfpk.SchurComplement(base_kernel, fixed_inputs=z)

      expected = broadcast_shapes(
          base_kernel.batch_shape, x_batch_shape, z_batch_shape) + num_x
      actual = k.apply(x, x, example_ndims=example_ndims).shape

      self.assertAllEqual(expected, actual)

  def testTensorShapesAreCorrect(self):
    for x1_example_ndims in range(0, 3):
      for x2_example_ndims in range(0, 3):
        # An integer generator.
        ints = itertools.count(start=2, step=1)
        feature_shape = [next(ints), next(ints)]

        x_batch_shape = [next(ints)]
        y_batch_shape = [next(ints), 1]
        z_batch_shape = [next(ints), 1, 1]

        num_x = [next(ints) for _ in range(x1_example_ndims)]
        num_y = [next(ints) for _ in range(x2_example_ndims)]
        num_z = [next(ints)]

        x_shape = x_batch_shape + num_x + feature_shape
        y_shape = y_batch_shape + num_y + feature_shape
        z_shape = z_batch_shape + num_z + feature_shape

        x = np.ones(x_shape, np.float64)
        y = np.ones(y_shape, np.float64)
        z = np.random.uniform(-1., 1., size=z_shape)

        base_kernel = tfpk.ExponentiatedQuadratic(
            amplitude=np.ones([next(ints), 1, 1, 1], np.float64),
            feature_ndims=len(feature_shape))

        k = tfpk.SchurComplement(base_kernel, fixed_inputs=z)

        expected = broadcast_shapes(
            base_kernel.batch_shape,
            x_batch_shape,
            y_batch_shape,
            z_batch_shape) + num_x + num_y

        mat = k.tensor(x, y,
                       x1_example_ndims=x1_example_ndims,
                       x2_example_ndims=x2_example_ndims)
        actual = mat.shape
        self.assertAllEqual(expected, actual)

  def testEmptyFixedInputs(self):
    base_kernel = tfpk.ExponentiatedQuadratic(1., 1.)
    fixed_inputs = tf.ones([0, 2], np.float32)
    schur = tfpk.SchurComplement(base_kernel, fixed_inputs)

    x = np.ones([4, 3], np.float32)
    y = np.ones([5, 3], np.float32)

    self.assertAllEqual(
        self.evaluate(base_kernel.matrix(x, y)),
        self.evaluate(schur.matrix(x, y)))

    # Test batch shapes
    base_kernel = tfpk.ExponentiatedQuadratic([1., 2.])
    fixed_inputs = tf.ones([0, 2], np.float32)
    schur = tfpk.SchurComplement(base_kernel, fixed_inputs)
    self.assertAllEqual([2], schur.batch_shape)
    self.assertAllEqual([2], self.evaluate(schur.batch_shape_tensor()))

  def testNoneFixedInputs(self):
    base_kernel = tfpk.ExponentiatedQuadratic(1., 1.)
    schur = tfpk.SchurComplement(base_kernel, fixed_inputs=None)

    x = np.ones([4, 3], np.float32)
    y = np.ones([5, 3], np.float32)

    self.assertAllEqual(
        self.evaluate(base_kernel.matrix(x, y)),
        self.evaluate(schur.matrix(x, y)))

  def testBaseKernelNoneDtype(self):
    # Test that we don't have problems when base_kernel has no explicit dtype
    # (ie, params are all None), but fixed_inputs has a different dtype than the
    # "common_dtype" default value of np.float32.
    fixed_inputs = np.arange(3, dtype=np.float64).reshape([3, 1])

    # Should raise when there's an explicit mismatch.
    with self.assertRaises(TypeError):
      schur_complement = tfpk.SchurComplement(
          tfpk.ExponentiatedQuadratic(np.float32(1)),
          fixed_inputs)

    # Should not throw an exception when the kernel doesn't get an explicit
    # dtype from its inputs.
    schur_complement = tfpk.SchurComplement(
        tfpk.ExponentiatedQuadratic(), fixed_inputs)
    schur_complement.matrix(fixed_inputs, fixed_inputs)

  def testSchurComplementWithPrecomputedDivisor(self):
    base_kernel = tfpk.ExponentiatedQuadratic([1., 2.])
    fixed_inputs = tf.ones([0, 2], np.float32)
    schur = tfpk.SchurComplement(base_kernel, fixed_inputs)
    schur_with_divisor = tfpk.SchurComplement.with_precomputed_divisor(
        base_kernel, fixed_inputs)

    x = np.ones([4, 3], np.float32)
    y = np.ones([5, 3], np.float32)

    self.assertAllClose(
        self.evaluate(schur_with_divisor.matrix(x, y)),
        self.evaluate(schur.matrix(x, y)))

  def testSchurComplementCholeskyFn(self):
    base_kernel = tfpk.ExponentiatedQuadratic([1., 2.])
    fixed_inputs = tf.ones([0, 2], np.float32)
    cholesky_fn = cholesky_util.make_cholesky_with_jitter_fn(jitter=1e-5)
    schur = tfpk.SchurComplement(
        base_kernel, fixed_inputs, cholesky_fn=cholesky_fn)
    schur_actual = tfpk.SchurComplement(base_kernel, fixed_inputs)
    self.assertEqual(cholesky_fn, schur.cholesky_fn)

    x = np.ones([4, 3], np.float32)
    y = np.ones([5, 3], np.float32)

    self.assertAllClose(
        self.evaluate(schur_actual.matrix(x, y)),
        self.evaluate(schur.matrix(x, y)))

  @test_util.disable_test_for_backend(
      disable_numpy=True, disable_jax=True,
      reason='Numpy and JAX have no notion of CompositeTensor/saved_model')
  def testPrecomputedDivisorCompositeTensor(self):
    base_kernel = tfpk.ExponentiatedQuadratic([1., 2.])
    fixed_inputs = tf.ones([0, 2], np.float32)
    schur_with_divisor = tfpk.SchurComplement.with_precomputed_divisor(
        base_kernel, fixed_inputs)

    x = np.ones([4, 3], np.float32)
    y = np.ones([5, 3], np.float32)

    flat = tf.nest.flatten(schur_with_divisor, expand_composites=True)
    unflat = tf.nest.pack_sequence_as(
        schur_with_divisor, flat, expand_composites=True)
    self.assertIsInstance(unflat, tfpk.SchurComplement)
    self.assertIsNotNone(
        schur_with_divisor._precomputed_divisor_matrix_cholesky)

    actual = self.evaluate(schur_with_divisor.matrix(x, y))

    self.assertAllClose(self.evaluate(unflat.matrix(x, y)), actual)

    @tf.function
    def matrix(k):
      return k.matrix(x, y)
    self.assertAllClose(actual, matrix(schur_with_divisor))
    self.assertAllClose(actual, matrix(unflat))

  def testBatchSlicePreservesPrecomputedDivisor(self):
    batch_shape = [4, 3]
    base_kernel = tfpk.ExponentiatedQuadratic(
        amplitude=self.evaluate(
            tf.exp(samplers.normal(batch_shape, seed=test_util.test_seed()))))
    fixed_inputs = self.evaluate(samplers.normal(batch_shape + [1, 2],
                                                 seed=test_util.test_seed()))
    schur = tfpk.SchurComplement(base_kernel, fixed_inputs)
    schur_with_divisor = tfpk.SchurComplement.with_precomputed_divisor(
        base_kernel, fixed_inputs)
    self.assertAllEqual(schur.batch_shape, batch_shape)
    self.assertAllEqual(schur_with_divisor.batch_shape, batch_shape)

    schur_sliced = schur[tf.newaxis, 0, ..., -2:]
    schur_with_divisor_sliced = schur_with_divisor[tf.newaxis, 0, ..., -2:]
    batch_shape_sliced = tf.ones(batch_shape)[tf.newaxis, 0, ..., -2:].shape
    self.assertAllEqual(schur_sliced.batch_shape, batch_shape_sliced)
    self.assertAllEqual(schur_with_divisor_sliced.batch_shape,
                        batch_shape_sliced)
    self.assertAllEqual(
        (schur_with_divisor_sliced
         ._precomputed_divisor_matrix_cholesky.shape[:-2]),
        batch_shape_sliced)

    x = np.ones([1, 2], np.float32)
    y = np.ones([3, 2], np.float32)
    self.assertAllClose(schur_with_divisor.matrix(x, y), schur.matrix(x, y))

if __name__ == '__main__':
  test_util.main()
