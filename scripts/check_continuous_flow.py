"""Verify native flow controls, calibration, cooling, and export-only tuning.

Usage: python scripts/check_continuous_flow.py EXE BASE_OPTIONS OUTPUT_DIR
"""
import json
import math
from pathlib import Path
import re
import subprocess
import sys
from check_continuous_generalization import solid, rectangle
from check_continuous_gcode import check


def run(exe, options, output, selected=()):
    output.mkdir(exist_ok=True, parents=True)
    model=output/'box.stl'
    solid(model,rectangle(12,8),rectangle(12,8),2)
    base=json.loads(options.read_text(encoding='utf-8-sig'))
    base.update(ce_flow_control='volumetric',ce_volumetric_flow='35',
        filament_max_volumetric_speed='35',filament_flow_ratio='1',print_flow_ratio='1',
        set_other_flow_ratios='0',outer_wall_speed='600',internal_solid_infill_speed='600',
        initial_layer_speed='600',initial_layer_infill_speed='600',slow_down_layers='0',
        slow_down_for_layer_cooling='0',machine_max_speed_x='1000,1000',
        machine_max_speed_y='1000,1000',machine_max_speed_z='40,40')
    cases={'fast':{},'calibrated':{'filament_flow_ratio':'0.9'},
        'limited':{'filament_max_volumetric_speed':'4'},
        'pressure-advance':{'enable_pressure_advance':'1','pressure_advance':'0.04'},
        'axis-limited':{'machine_max_speed_x':'30,30','machine_max_speed_y':'30,30'},
        'cooling':{'slow_down_for_layer_cooling':'1','slow_down_layer_time':'30'},
        'normal-speeds':{'ce_flow_control':'process_speeds','outer_wall_speed':'60','internal_solid_infill_speed':'120'},
        'automatic':{'ce_flow_control':'automatic','outer_wall_speed':'60','internal_solid_infill_speed':'120'},
        'adhesion':{'brim_type':'outer_only','brim_width':'3','skirt_loops':'1'},
        'absolute-e':{'use_relative_e_distances':'0'}}
    results=json.loads((output/'results.json').read_text()) if selected else {}
    filament_area=math.pi*(float(base['filament_diameter'])/2)**2
    for name, changes in cases.items():
        if selected and name not in selected: continue
        config=dict(base,**changes)
        settings=output/(name+'.json')
        settings.write_text(json.dumps(config,indent=2))
        gcode=output/(name+'.gcode')
        command=[str(exe.resolve()),'--reslice-native' if name=='fast' else '--slice-native',str(model),str(gcode),'1',str(settings)]
        with (output/(name+'.log')).open('w') as log:
            subprocess.run(command,stdout=log,stderr=log,check=True,timeout=240)
        audit=check(gcode)
        planned=float(re.search(r'; continuous planned volume = ([0-9.]+)',gcode.read_text()).group(1))
        expected=planned*float(config['filament_flow_ratio'])
        assert math.isclose(audit['filament_mm']*filament_area,expected,rel_tol=1e-7), (name,'incorrect deposited volume',audit,expected)
        flow=audit['nominal_filament_feed_mm_s'][1]*filament_area
        assert flow <= float(config['filament_max_volumetric_speed'])+0.003,(name,'exceeded volumetric limit',flow)
        results[name]=dict(audit,maximum_flow_mm3_s=flow,planned_volume_mm3=planned)
        if name=='fast':
            assert flow>34.9, '35 mm3/s target was not reached when speed limits allowed it'
            results['resliced']=check(str(gcode)+'.resliced.gcode')
            assert results['resliced']['nominal_filament_feed_mm_s'][1]*filament_area <=2.503
        if name=='pressure-advance':
            assert re.search(r'SET_PRESSURE_ADVANCE ADVANCE=0\.04',gcode.read_text())
        if name=='axis-limited':
            assert all(audit['max_axis_speed_mm_s'][k]<=30.001 for k in 'XY')
        (output/'results.json').write_text(json.dumps(results,indent=2))
        print(name, 'passed',round(flow,3),'mm3/s',flush=True)
    assert results['cooling']['nominal_seconds']>results['fast']['nominal_seconds']*2
    assert results['normal-speeds']['nominal_seconds']<results['automatic']['nominal_seconds']
    assert math.isclose(results['calibrated']['filament_mm']/results['fast']['filament_mm'],.9,rel_tol=1e-7)
    print('All flow, volume, cooling, speed, continuity, and cache checks passed')


if __name__=='__main__':
    run(*(Path(x) for x in sys.argv[1:4]), sys.argv[4:])
