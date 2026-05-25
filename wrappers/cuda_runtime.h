/* cuda_runtime.h - CUDA-to-MACA API compatibility header
   Maps standard CUDA API symbols to MACA (mc_*) equivalents.
   Include this instead of the real cuda_runtime.h on MACA systems. */

#ifndef __MACA_CUDA_RUNTIME_COMPAT_H__
#define __MACA_CUDA_RUNTIME_COMPAT_H__

/* Include the MACA runtime (equivalent of cuda_runtime.h) */
#include <mcr/mc_runtime.h>

/* ---- Type mappings ---- */
#define cudaError_t  mcError_t
#define cudaStream_t mcStream_t
#define cudaEvent_t  mcEvent_t
#define cudaMemcpyKind mcMemcpyKind

/* ---- Error code mappings ---- */
#define cudaSuccess            mcSuccess
#define cudaErrorNotReady      mcErrorNotReady
#define cudaErrorUnknown       mcErrorUnknown
#define cudaErrorInvalidValue  mcErrorInvalidValue

/* ---- Stream constants ---- */
#define cudaStreamNonBlocking  mcStreamNonBlocking
#define cudaStreamDefault      mcStreamDefault
#define cudaStreamPerThread    mcStreamPerThread

/* ---- Memory copy kinds ---- */
#define cudaMemcpyHostToHost     mcMemcpyHostToHost
#define cudaMemcpyHostToDevice   mcMemcpyHostToDevice
#define cudaMemcpyDeviceToHost   mcMemcpyDeviceToHost
#define cudaMemcpyDeviceToDevice mcMemcpyDeviceToDevice
#define cudaMemcpyDefault        mcMemcpyDefault

/* ---- Function mappings ---- */
#define cudaMemcpy               mcMemcpy
#define cudaMemcpyAsync          mcMemcpyAsync
#define cudaMemcpyToSymbol       mcMemcpyToSymbol
#define cudaMalloc               mcMalloc
#define cudaFree                 mcFree
#define cudaMallocHost           mcMallocHost
#define cudaFreeHost             mcFreeHost
#define cudaMemset               mcMemset

#define cudaStreamCreate           mcStreamCreate
#define cudaStreamCreateWithFlags  mcStreamCreateWithFlags
#define cudaStreamDestroy          mcStreamDestroy
#define cudaStreamSynchronize      mcStreamSynchronize
#define cudaStreamWaitEvent        mcStreamWaitEvent
#define cudaStreamQuery            mcStreamQuery

#define cudaEventCreate          mcEventCreate
#define cudaEventCreateWithFlags mcEventCreateWithFlags
#define cudaEventDestroy         mcEventDestroy
#define cudaEventRecord          mcEventRecord
#define cudaEventSynchronize     mcEventSynchronize
#define cudaEventQuery           mcEventQuery
#define cudaEventElapsedTime     mcEventElapsedTime

#define cudaDeviceSynchronize  mcDeviceSynchronize
#define cudaSetDevice          mcSetDevice
#define cudaGetDevice          mcGetDevice
#define cudaGetDeviceCount     mcGetDeviceCount
#define cudaGetDeviceProperties mcGetDeviceProperties

#define cudaGetErrorString   mcGetErrorString
#define cudaGetErrorName     mcGetErrorName
#define cudaPeekAtLastError  mcPeekAtLastError
#define cudaGetLastError     mcGetLastError

#define cudaDeviceReset      mcDeviceReset
#define cudaDeviceSetLimit   mcDeviceSetLimit
#define cudaDeviceGetLimit   mcDeviceGetLimit

#define cudaFuncSetCacheConfig    mcFuncSetCacheConfig
#define cudaFuncGetAttributes     mcFuncGetAttributes
#define cudaFuncSetAttribute      mcFuncSetAttribute

#define cudaDeviceGetAttribute    mcDeviceGetAttribute
#define cudaDeviceSetCacheConfig  mcDeviceSetCacheConfig

#define cudaOccupancyMaxPotentialBlockSize mcOccupancyMaxPotentialBlockSize
#define cudaOccupancyMaxActiveBlocksPerMultiprocessor mcOccupancyMaxActiveBlocksPerMultiprocessor

#define cudaHostAlloc         mcHostAlloc
#define cudaHostRegister      mcHostRegister
#define cudaHostUnregister    mcHostUnregister
#define cudaHostGetDevicePointer mcHostGetDevicePointer
#define cudaHostGetFlags      mcHostGetFlags

#define cudaDeviceCanAccessPeer  mcDeviceCanAccessPeer
#define cudaDeviceEnablePeerAccess mcDeviceEnablePeerAccess
#define cudaDeviceDisablePeerAccess mcDeviceDisablePeerAccess

#endif /* __MACA_CUDA_RUNTIME_COMPAT_H__ */
