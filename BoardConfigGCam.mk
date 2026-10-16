# SPDX-License-Identifier: Apache-2.0
FLORAL_GCAM_PATH := $(patsubst %/,%,$(dir $(lastword $(MAKEFILE_LIST))))
ifeq ($(wildcard $(FLORAL_GCAM_PATH)/generated/config.mk),)
$(error Prepare the Floral GCam plugin before including BoardConfigGCam.mk)
endif
include $(FLORAL_GCAM_PATH)/generated/config.mk
ifeq ($(FLORAL_GCAM_ADD_SIGNER),true)
PRODUCT_PRIVATE_SEPOLICY_DIRS += $(FLORAL_GCAM_PATH)/generated/sepolicy
endif
