/**
 * pytorch_npu_helper.hpp: Header for PyTorch-to-ACL tensor conversion utilities.
 *
 * Provides:
 *   ConvertType(): Converts an at::Tensor to an aclTensor* for use with ACLNN APIs.
 *   Release(): Safely destroys an aclTensor*.
 *
 * This file is operator-agnostic within the optional PyTorch pybind11 adapter path.
 */
#pragma once

#include <torch/extension.h>
#include <aclnn/acl_meta.h>

/**
 * Convert a PyTorch tensor to an ACL tensor handle.
 *
 * Conversion notes:
 * - The returned aclTensor* does not own the data; PyTorch owns the tensor lifetime.
 * - storageDims is passed as 1D (&storage_len, 1), matching the ACL API expectation.
 *   Passing the actual shape dimensions here will cause silent data corruption.
 * - The caller releases the handle with Release() when done.
 */
aclTensor* ConvertType(const at::Tensor& tensor);

/**
 * Safely destroy an aclTensor handle (null-safe).
 */
void Release(aclTensor* tensor);
