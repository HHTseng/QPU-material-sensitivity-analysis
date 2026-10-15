"""Draw device geometry, recorded source sites, and the calculation sequence."""

import argparse,hashlib,json
from pathlib import Path
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle,FancyBboxPatch,FancyArrowPatch
import yaml

root=Path(__file__).resolve().parents[1]
parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--design',type=Path,default=Path(__file__).with_name('spatial_sites.json'),help='recorded spatial design JSON')
parser.add_argument('--output-dir',type=Path,default=Path(__file__).parent,help='directory for PNG, SVG and provenance JSON')
args=parser.parse_args(); out=args.output_dir.resolve(); out.mkdir(parents=True,exist_ok=True)
source=args.design.resolve(); design=json.loads(source.read_text())
reference=yaml.safe_load((root/'studies/reference.yaml').read_text())['fixed']
catalog=yaml.safe_load((root/'legacy/material_catalog.yaml').read_text())
substrate_um=reference['setSubThickness_um']; aluminum_nm=1000*reference['setTopThickness_um']
niobium_nm=1000*catalog['top_films']['Nb']['thickness_um']; copper_um=catalog['bottom_films']['Cu']['thickness_um']
width,height=reference['setSubWidth_mm'],reference['setSubHeight_mm']; span=reference['position_half_span_mm']
n_junctions=len(reference['electrode_x_mm']); junction_w=reference['electrode_width_um']; junction_h=reference['electrode_height_um']
energy_eV=reference['gun_energy_eV']
plt.rcParams.update({'font.family':'DejaVu Sans','font.size':11,'axes.labelsize':11,'axes.titlesize':12,
                     'svg.fonttype':'none','svg.hashsalt':'material-scan-documentation-v1','savefig.facecolor':'white','figure.facecolor':'white'})
navy='#18324b'; blue='#30658a'; teal='#238b85'; copper='#b87347'; orange='#d56c35'; light='#e8f1f7'; grey='#596774'

def save(fig,name):
    fig.savefig(out/(name+'.png'),dpi=180,bbox_inches='tight',pad_inches=.18)
    fig.savefig(out/(name+'.svg'),bbox_inches='tight',pad_inches=.18,metadata={'Date':None})
    svg=out/(name+'.svg'); svg.write_text('\n'.join(line.rstrip() for line in svg.read_text().splitlines())+'\n')
    plt.close(fig)

fig,ax=plt.subplots(figsize=(12,5.6));ax.set(xlim=(0,14),ylim=(0,7));ax.axis('off')
ax.text(.25,6.65,'Phonons reach different film regions; only junction QPs enter the response',fontsize=14,color=navy,weight='bold')
ax.text(.25,6.23,'Si / Nb / Cu reference with fixed Al junction regions',fontsize=11,color=grey)
ax.add_patch(Rectangle((.7,1.65),7.55,2.85,facecolor=light,edgecolor=blue,linewidth=1.4))
ax.add_patch(Rectangle((.7,1.3),7.55,.35,facecolor=copper,edgecolor=copper))
ax.add_patch(Rectangle((.7,4.5),7.55,.21,facecolor=teal,edgecolor=teal))
for x in (1.8,4.45,6.95):
    ax.add_patch(Rectangle((x,4.5),.54,.37,facecolor=orange,edgecolor='#91451e',linewidth=1.1))
ax.text(4.5,2.65,'Si',ha='center',va='center',fontsize=24,color=blue,alpha=.75)
ax.plot(1.35,3.7,'*',markersize=14,color=navy)
ax.text(2.6,3.8,f'source phonon\n{1000*energy_eV:g} meV',fontsize=10,color=navy,ha='left')
for points in [[(1.35,3.7),(2.15,4.5)],[(1.35,3.7),(3.2,1.66),(5.2,4.5)],[(1.35,3.7),(6.1,1.66)]]:
    for a,b in zip(points,points[1:]):
        ax.add_patch(FancyArrowPatch(a,b,arrowstyle='-|>',mutation_scale=12,color=blue,linewidth=1.35,alpha=.85))
ax.add_patch(FancyArrowPatch((7.2,4.93),(7.2,5.54),arrowstyle='-|>',mutation_scale=14,color=orange,linewidth=1.5))
ax.text(7.2,5.7,'counted QPs',ha='center',fontsize=10,color=orange,weight='bold')
for y,origin,title,detail,color in [(5.04,(7.5,4.74),f'Al junction regions  |  {aluminum_nm:g} nm',f'{n_junctions} collection regions; {junction_w:g} × {junction_h:g} μm² each',orange),
        (4.0,(8.25,4.59),f'Nb upper film  |  {niobium_nm:g} nm','Competing phonon absorption',teal),
        (2.91,(8.25,3.0),f'Si substrate  |  {substrate_um:g} μm','Elastic scattering and downconversion',blue),
        (1.81,(8.25,1.48),f'Cu bottom film  |  {copper_um:g} μm','Normal-metal phonon absorption',copper)]:
    ax.annotate('',xy=origin,xytext=(9.0,y-.02),arrowprops={'arrowstyle':'-','color':color,'linewidth':1.1})
    ax.text(9.12,y,title,fontsize=11,color=color,weight='bold',va='center')
    ax.text(9.12,y-.36,detail,fontsize=9.3,color=grey,va='center')
ax.text(.7,.7,'Boundary schematic; thicknesses and paths are not to scale.',fontsize=10,color=grey)
ax.text(.7,.32,'Upper- and bottom-film absorptions are outside the recorded junction-QP response.',fontsize=10,color=grey)
save(fig,'device_geometry')

xy=np.array(design['sites_mm'])[:,:2]; labels=design['stratum']; weights=design['stratum_weights']; regions=list(weights)
centers=np.array([reference['electrode_x_mm'],reference['electrode_y_mm']]).T
half=np.array([reference['electrode_width_um'],reference['electrode_height_um']])/2000
colors=['#b23a48','#e99f38','#3f8c73','#4c78a8','#8a94a1']
short=['inside junction','0–0.05 mm','0.05–0.20 mm','0.20–0.50 mm','remaining area']
fig=plt.figure(figsize=(12,6.3));ax=fig.add_axes([.07,.14,.54,.73]);ax.set_aspect('equal')
for color,h in zip(colors,regions):
    points=xy[np.array(labels)==h]
    ax.scatter(points[:,0],points[:,1],s=10,color=color,alpha=.8,zorder=2)
for center in centers:
    ax.add_patch(Rectangle(center-half,*2*half,facecolor=navy,edgecolor=navy,linewidth=.55,zorder=4))
ax.scatter(centers[:,0],centers[:,1],marker='+',s=32,color=navy,linewidths=.9,zorder=4)
ax.add_patch(Rectangle((-width/2,-height/2),width,height,fill=False,edgecolor=navy,linewidth=1.1,zorder=0))
ax.add_patch(Rectangle((-span,-span),2*span,2*span,fill=False,edgecolor=navy,linestyle='--',linewidth=1.0,zorder=3))
ax.set(xlim=(-width/2-.2,width/2+.2),ylim=(-height/2-.2,height/2+.2),xlabel='x [mm]',ylabel='y [mm]');ax.grid(alpha=.13)
fig.text(.07,.94,f'Recorded {len(xy)}-site spatial design',fontsize=15,color=navy,weight='bold')
fig.text(.07,.895,f'{width:g} × {height:g} mm² substrate; uniform injection over the dashed {2*span:g} × {2*span:g} mm² region',fontsize=10.8,color=grey)
fig.text(.655,.845,'Distance from nearest\njunction rectangle',fontsize=10.5,color=navy,weight='bold')
fig.text(.86,.845,'Sites',fontsize=10.5,color=navy,weight='bold',ha='right')
fig.text(.985,.845,'Area',fontsize=10.5,color=navy,weight='bold',ha='right')
for i,(h,color,name) in enumerate(zip(regions,colors,short)):
    y=.755-i*.061
    fig.text(.65,y,'●',color=color,fontsize=12,va='center')
    fig.text(.67,y,name,fontsize=10.3,color=navy,va='center')
    fig.text(.86,y,str(labels.count(h)),fontsize=10.3,color=navy,ha='right',va='center')
    fig.text(.985,y,f'{100*weights[h]:.4f}%',fontsize=10.3,color=navy,ha='right',va='center')
fig.text(.655,.425,'Near junctions are oversampled.\nArea weights correct their contribution.',fontsize=10.5,color=grey,linespacing=1.45)
zoom=fig.add_axes([.69,.12,.23,.23]);zoom.set_aspect('equal')
for color,h in zip(colors,regions):
    points=xy[np.array(labels)==h];zoom.scatter(points[:,0],points[:,1],s=10,color=color,alpha=.85)
zoom.add_patch(Rectangle(-half,*2*half,facecolor=navy,edgecolor=navy,linewidth=1.))
zoom.set(xlim=(-.3,.3),ylim=(-.3,.3),xticks=[-.2,0,.2],yticks=[-.2,0,.2]);zoom.tick_params(labelsize=8)
zoom.set_title('Centre junction: magnified view [mm]',fontsize=9,color=navy,pad=6);zoom.grid(alpha=.15)
fig.text(.07,.045,'Actual recorded sites and area fractions; no transport results are shown. Junction markers in the full view are enlarged.',fontsize=9.5,color=grey)
save(fig,'spatial_sampling')

fig,ax=plt.subplots(figsize=(12,5.7));ax.set(xlim=(-.55,12),ylim=(0,5.7));ax.axis('off')
ax.text(.25,5.4,'One physical calculation for every study',fontsize=15,color=navy,weight='bold')
ax.text(.25,5.02,'Sensitivity · linked materials · property search · confirmation · spatial refinement · tolerance',fontsize=10.5,color=grey)
positions=[(.2,3.55),(3.2,3.55),(6.2,3.55),(9.2,3.55),(9.2,1.6),(6.2,1.6),(3.2,1.6),(.2,1.6)]
titles=['Material properties','Physical checks','Phonon transport','Recorded hits','Junction QPs','Weighted statistics','Scientific checks','Measured materials']
lines=['Measured records or\nselected property values','Units, elasticity, gaps,\nmaterial density','Geant4 / G4CMP\nrecorded sites and seeds','Deposited energy,\nposition and arrival time','q = round(Edep / Δ)\non Al collection regions','Yield, uncertainty and\npaired differences','Convergence, tolerance\nand independent layouts','Measured feature coverage;\nre-simulate complete records']
for i,((x,y),title,line) in enumerate(zip(positions,titles,lines)):
    color=teal if i in (1,6) else orange if i==4 else blue
    ax.add_patch(FancyBboxPatch((x,y),2.58,.98,boxstyle='round,pad=.035,rounding_size=.07',facecolor='#f4f7fa',edgecolor=color,linewidth=1.2))
    ax.text(x+1.29,y+.72,title,ha='center',va='center',fontsize=11,color=color,weight='bold')
    ax.text(x+1.29,y+.31,line,ha='center',va='center',fontsize=9,color=navy,linespacing=1.35)
for i in range(3):
    ax.add_patch(FancyArrowPatch((positions[i][0]+2.63,4.04),(positions[i+1][0]-.08,4.04),arrowstyle='-|>',mutation_scale=13,color=navy,linewidth=1.1))
ax.add_patch(FancyArrowPatch((10.49,3.49),(10.49,2.67),arrowstyle='-|>',mutation_scale=13,color=navy,linewidth=1.1))
for i in range(4,7):
    ax.add_patch(FancyArrowPatch((positions[i][0]-.07,2.09),(positions[i+1][0]+2.65,2.09),arrowstyle='-|>',mutation_scale=13,color=navy,linewidth=1.1))
ax.text(4.49,2.98,'Invalid physical inputs stop here.\nFailed or incomplete simulations have no yield.',ha='center',fontsize=9.4,color=grey,linespacing=1.35)
ax.add_patch(FancyArrowPatch((7.49,1.54),(7.49,.68),arrowstyle='-',color=teal,linewidth=1.1))
ax.add_patch(FancyArrowPatch((7.49,.68),(-.3,.68),arrowstyle='-',color=teal,linewidth=1.1))
ax.add_patch(FancyArrowPatch((-.3,.68),(-.3,4.04),arrowstyle='-',color=teal,linewidth=1.1))
ax.add_patch(FancyArrowPatch((-.3,4.04),(.1,4.04),arrowstyle='-|>',mutation_scale=13,color=teal,linewidth=1.1))
ax.text(4.49,.25,'Search updates the selected properties; confirmations use independent layouts and unused random streams.',ha='center',fontsize=10,color=grey)
save(fig,'workflow')

source_name=str(source.relative_to(root)) if source.is_relative_to(root) else str(source)
sources=['studies/reference.yaml','legacy/material_catalog.yaml','legacy/macros/sensitivity_template_beamOn1e6.mac','figures/draw.py',source_name]
provenance={'software':{'matplotlib':matplotlib.__version__,'numpy':np.__version__,'font':'DejaVu Sans'},
 'source_sha256':{p:hashlib.sha256((root/p).read_bytes()).hexdigest() for p in sources},
 'device_geometry':{'meaning':'boundary schematic, not to scale; arrows are illustrative, not simulated tracks',
   'baseline':['Si substrate','Nb nonjunction upper film','Cu normal-metal bottom film','Al junction collection regions'],
   'thicknesses':{'substrate_um':substrate_um,'Al_junction_nm':aluminum_nm,'Nb_upper_nm':niobium_nm,'Cu_bottom_um':copper_um},
   'collection_regions':n_junctions,'collection_rectangle_um':[junction_w,junction_h],'source_energy_eV':energy_eV,
   'model_role_check':'Geant4 Main/src/PhononDetectorConstruction.cc film material-property tables; PhononSensitivity.cc Junction hit selection'},
 'spatial_sampling':{'source':source_name,'design_hash':design['design_hash'],
   'site_count':len(xy),'stratum_weights':weights,'stratum_counts':{h:labels.count(h) for h in regions},
   'substrate_extent_mm':[-width/2,width/2,-height/2,height/2],'injection_extent_mm':[-span,span,-span,span],
   'meaning':'recorded site coordinates; marker size enlarged for visibility; no simulated response data'},
 'workflow':{'meaning':'scientific calculation and study relationships; no timing or result claims'}}
(out/'provenance.json').write_text(json.dumps(provenance,indent=2)+'\n')
