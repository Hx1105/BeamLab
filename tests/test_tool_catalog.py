from beamlab import gateway


def test_gateway_presents_exact_official_tool_definitions_without_reimplementing_them():
    assert hasattr(gateway,'filter_tool_catalog'), 'Explicit seven-tool presentation absent'
    tools=[{'type':'function','function':{'name':name,'parameters':{'type':'object'}}} for name in gateway.DEVICE_TOOLS]
    extra={'type':'function','function':{'name':'shell'}}
    assert gateway.filter_tool_catalog(tools+[extra])==tools
    assert all(a is b for a,b in zip(gateway.filter_tool_catalog(tools+[extra]),tools))
