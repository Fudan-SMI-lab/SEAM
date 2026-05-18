/**
 * pytorch_npu_helper.cpp: PyTorch tensor to ACL tensor conversion.
 *
 * This file provides the bridge between PyTorch's at::Tensor and Ascend's aclTensor.
 * It is operator-agnostic within the optional PyTorch pybind11 adapter path.
 *
 * KEY LESSON (from production debugging):
 *   aclCreateTensor's storageDims parameter was 1D in this adapter: (&storage_len, 1).
 *   Passing the tensor's actual shape here caused silent data corruption
 *   because the ACL runtime interprets storageDims as a flat byte-length descriptor.
 *
 * The 9-argument aclCreateTensor signature:
 *   1. viewDims: pointer to shape array, for example [batch, height, width]
 *   2. viewDimsNum: number of dimensions
 *   3. dataType: ACL data type enum
 *   4. stride: pointer to stride array
 *   5. storageOffset: element offset into storage
 *   6. format: tensor format, ACL_FORMAT_ND for generic N-dimensional
 *   7. storageDims: (&total_numel, 1), not the shape
 *   8. storageDimsNum: 1
 *   9. dataPtr: raw device pointer to tensor data
 */
#include <vector>

#include "pytorch_npu_helper.hpp"

// ============================================================================
// aclCreateTensor, extern "C" declaration
// ============================================================================
// This function is provided by libascendcl.so but not declared in public headers.
// We declare it manually with the exact 9-argument signature.
extern "C" aclTensor* aclCreateTensor(
    const int64_t *viewDims,
    uint64_t viewDimsNum,
    aclDataType dataType,
    const int64_t* stride,
    int64_t storageOffset,
    aclFormat format,
    const int64_t* storageDims,
    uint64_t storageDimsNum,
    void* dataPtr);

namespace {

// ============================================================================
// PyTorch ScalarType to ACL DataType mapping
// ============================================================================
aclDataType ToAclDataType(at::ScalarType scalar_type)
{
    switch (scalar_type) {
        case at::kBool:         return ACL_BOOL;
        case at::kByte:         return ACL_UINT8;
        case at::kChar:         return ACL_INT8;
        case at::kShort:        return ACL_INT16;
        case at::kInt:          return ACL_INT32;
        case at::kLong:         return ACL_INT64;
        case at::kHalf:         return ACL_FLOAT16;
        case at::kFloat:        return ACL_FLOAT;
        case at::kDouble:       return ACL_DOUBLE;
        case at::kBFloat16:     return ACL_BF16;
        case at::kComplexFloat: return ACL_COMPLEX64;
        case at::kComplexDouble:return ACL_COMPLEX128;
        default:                return ACL_DT_UNDEFINED;
    }
}

}  // namespace

// ============================================================================
// ConvertType, the conversion function
// ============================================================================
aclTensor* ConvertType(const at::Tensor& tensor)
{
    const aclDataType data_type = ToAclDataType(tensor.scalar_type());
    if (data_type == ACL_DT_UNDEFINED) {
        return nullptr;
    }

    const auto sizes = tensor.sizes();
    const auto strides = tensor.strides();
    std::vector<int64_t> shape(sizes.begin(), sizes.end());
    std::vector<int64_t> stride_vec(strides.begin(), strides.end());

    // Observed pitfall: storageDims is 1D with total element count.
    // Passing shape dimensions here causes silent corruption.
    int64_t storage_len = tensor.numel();
    return aclCreateTensor(
        shape.data(),        // viewDims: actual tensor shape
        shape.size(),        // viewDimsNum: number of dimensions
        data_type,           // dataType: mapped ACL type
        stride_vec.data(),   // stride: PyTorch stride array
        tensor.storage_offset(), // storageOffset: offset in elements
        ACL_FORMAT_ND,       // format: generic N-dimensional
        &storage_len,        // storageDims: 1D (&numel, 1)
        1,                   // storageDimsNum: 1
        tensor.data_ptr());  // dataPtr: raw device pointer
}

// ============================================================================
// Release, safe cleanup
// ============================================================================
void Release(aclTensor* tensor)
{
    if (tensor != nullptr) {
        aclDestroyTensor(tensor);
    }
}
