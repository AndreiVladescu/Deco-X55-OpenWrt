include $(TOPDIR)/rules.mk

PKG_NAME:=deco-mesh
PKG_VERSION:=0.4
PKG_RELEASE:=1
PKG_LICENSE:=GPL-2.0-only
PKG_MAINTAINER:=Andrei Vladescu

include $(INCLUDE_DIR)/package.mk

define Package/deco-mesh
  SECTION:=net
  CATEGORY:=Network
  TITLE:=Push-button pairing for a batman-adv/802.11s mesh (Deco-style onboarding)
  DEPENDS:=+kmod-batman-adv +batctl-full +uclient-fetch +jsonfilter +uhttpd +rpcd +wpad-mbedtls
  PKGARCH:=all
endef

define Package/deco-mesh/description
  Short-press the button on a node that is already in the mesh to open a WPS-protected pairing
  window, then on the new node. The new node asks to join, the request is approved in LuCI
  (Network > Mesh nodes), and the node receives the mesh settings and joins.
endef

define Package/deco-mesh/conffiles
/etc/config/decomesh
endef

define Build/Compile
endef

define Package/deco-mesh/install
	$(CP) ./files/* $(1)/
endef

$(eval $(call BuildPackage,deco-mesh))
