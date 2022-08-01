<!-- included start from bgp/reset-bgp-peer-group.xml.i -->
<tagNode name="peer-group">
  <properties>
    <help>Reset all members of peer-group</help>
    <completionHelp>
      <path>protocols bgp peer-group</path>
    </completionHelp>
  </properties>
  <command>${vyos_op_scripts_dir}/vtysh_wrapper.sh $@</command>
  <children>
    #include <include/bgp/reset-bgp-neighbor-options.xml.i>
  </children>
</tagNode>
<!-- included end -->