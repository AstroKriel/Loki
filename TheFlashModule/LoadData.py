## START OF LIBRARY


## ###############################################################
## MODULES
## ###############################################################
import h5py
import numpy as np

from numba import njit

## load user defined modules
from TheAnalysisModule import WWFields
from TheUsefulModule import WWFnF, WWFuncs, WWLists
from TheFlashModule import FileNames


## ###############################################################
## FUNCTIONS FOR LOADING FLASH DATA
## ###############################################################
def readFromChkFile(fielpath_chkfile, dset, param):
  with h5py.File(fielpath_chkfile, "r") as h5file:
    for _param, _value in h5file[dset]:
      if f"b'{param}" in str(_param): return int(_value)
  return None

def reformatFlashField(field, num_blocks, num_procs):
  xblocks, yblocks, zblocks = num_blocks
  iprocs, jprocs, kprocs = num_procs
  ## initialise the organised field
  field_sorted = np.zeros(
    shape = (
      zblocks*kprocs,
      yblocks*jprocs,
      xblocks*iprocs,
    ),
    dtype = np.float32,
    order = "C"
  )
  ## [ num_cells_per_block, yblocks, zblocks, xblocks ] -> [ kprocs*zblocks, jprocs*yblocks, iprocs*xblocks ]
  block_index = 0
  for kproc_index in range(kprocs):
    for jproc_index in range(jprocs):
      for iproc_index in range(iprocs):
        field_sorted[
          kproc_index * zblocks : (kproc_index+1) * zblocks,
          jproc_index * yblocks : (jproc_index+1) * yblocks,
          iproc_index * xblocks : (iproc_index+1) * xblocks,
        ] = field[block_index, :, :, :]
        block_index += 1
  ## rearrange indices [ z, y, x ] -> [ x, y, z ]
  field_sorted = np.transpose(field_sorted, [2, 1, 0])
  return field_sorted

@WWFuncs.timeFunc
def loadFlashDataCube(
    filepath_file, num_blocks, num_procs, field_name,
    bool_norm_rms     = False,
    bool_print_h5keys = False
  ):
  ## open hdf5 file stream
  with h5py.File(filepath_file, "r") as h5file:
    ## create list of field-keys to extract from hdf5 file
    list_keys_stored = list(h5file.keys())
    list_keys_used = [
      key
      for key in list_keys_stored
      if key.startswith(field_name)
    ]
    if len(list_keys_used) == 0: raise Exception(f"Error: field-name '{field_name}' not found in {filepath_file}")
    ## check which keys are stored
    if bool_print_h5keys: 
      print("--------- All the keys stored in the FLASH hdf5 file:\n\t" + "\n\t".join(list_keys_stored))
      print("--------- All the keys that were used: " + str(list_keys_used))
    ## extract fields from hdf5 file
    field_group_comp = [
      np.array(h5file[key])
      for key in list_keys_used
    ]
    ## close file stream
    h5file.close()
  ## reformat data
  field_sorted_group_comps = []
  for field_comp in field_group_comp:
    field_comp_sorted = reformatFlashField(field_comp, num_blocks, num_procs)
    ## normalise by rms-value
    if bool_norm_rms: field_comp_sorted /= WWFields.sfieldRMS(field_comp_sorted)
    field_sorted_group_comps.append(field_comp_sorted)
  ## return spatial-components of data
  return np.squeeze(field_sorted_group_comps)

def loadAllFlashDataCubes(
    directory, field_name, dict_sim_inputs,
    start_time  = 0,
    end_time    = np.inf,
    subset_step = 1
  ):
  outputs_per_t_turb = dict_sim_inputs["outputs_per_t_turb"]
  ## get all plt files in the directory
  list_filenames = WWFnF.getFilesInDirectory(
    directory             = directory,
    filename_starts_with  = FileNames.FILENAME_FLASH_PLT_FILES,
    filename_not_contains = "spect",
    loc_file_index        = 4,
    file_start_index      = outputs_per_t_turb * start_time,
    file_end_index        = outputs_per_t_turb * end_time
  )
  ## find min and max colorbar limits
  field_min = np.nan
  field_max = np.nan
  ## save field slices and simulation times
  field_group_t = []
  list_t_turb = []
  for filename, _ in WWLists.loopListWithUpdates(list_filenames[::subset_step]):
    field_magnitude = loadFlashDataCube(
      filepath_file = f"{directory}/{filename}",
      num_blocks    = dict_sim_inputs["num_blocks"],
      num_procs     = dict_sim_inputs["num_procs"],
      field_name    = field_name
    )
    list_t_turb.append( float(filename.split("_")[-1]) / outputs_per_t_turb )
    field_group_t.append( field_magnitude[:,:] ) # TODO: this won't work anymore
    field_min = np.nanmin([ field_min, np.nanmin(field_magnitude[:,:]) ])
    field_max = np.nanmax([ field_max, np.nanmax(field_magnitude[:,:]) ])
  print(" ")
  return {
    "list_t_turb" : list_t_turb,
    "field_group_t"   : field_group_t,
    "field_bounds"    : [ field_min, field_max ]
  }

def loadVIData(
    directory, t_turb,
    field_index  = None,
    field_name   = None,
    time_start   = 1,
    time_end     = np.inf,
    bool_debug   = False,
    bool_verbose = False
  ):
  ## define which quantities to read in
  time_index = 0
  if field_index is None:
    ## check that a variable name has been provided
    if field_name is None: raise Exception("Error: need to provide either a field-index or field-name")
    ## check which formatting the output file uses
    with open(f"{directory}/{FileNames.FILENAME_FLASH_VI_DATA}", "r") as fp:
      file_first_line = fp.readline()
      bool_format_new = "#01_time" in file_first_line.split() # new version of file indexes from 1
    ## get index of field in file
    if   "kin"  in field_name.lower(): field_index = 9  if bool_format_new else 6
    elif "mag"  in field_name.lower(): field_index = 11 if bool_format_new else 29
    elif "mach" in field_name.lower(): field_index = 13 if bool_format_new else 8
    else: raise Exception(f"Error: reading in {FileNames.FILENAME_FLASH_VI_DATA}")
  ## initialise quantities to track traversal
  data_time  = []
  data_field = []
  prev_time  = np.inf
  with open(f"{directory}/{FileNames.FILENAME_FLASH_VI_DATA}", "r") as fp:
    num_fields = len(fp.readline().split())
    ## read data in backwards
    for line in reversed(fp.readlines()):
      data_split_columns = line.replace("\n", "").split()
      ## only read where every field has been processed
      if not(len(data_split_columns) == num_fields): continue
      ## ignore comments
      if "#" in data_split_columns[time_index]:  continue
      if "#" in data_split_columns[field_index]: continue
      ## compute time in units of eddy turnover time
      this_time = float(data_split_columns[time_index]) / t_turb
      ## only read data that has progressed in time
      if this_time < prev_time:
        data_val = float(data_split_columns[field_index])
        ## something might have gone wrong: it is very unlikely to encounter a 0-value exactly
        if (data_val == 0.0) and (0 < this_time):
          warning_message = f"{FileNames.FILENAME_FLASH_VI_DATA}: value of field-index = {field_index} is 0.0 at time = {this_time}"
          if bool_debug: raise Exception(f"Error: {warning_message}")
          if bool_verbose: print(f"Warning: {warning_message}")
          continue
        ## store data
        data_time.append(this_time)
        data_field.append(data_val)
        ## step backwards
        prev_time = this_time
  ## re-order data
  data_time  = data_time[::-1]
  data_field = data_field[::-1]
  ## subset data based on provided time bounds
  index_start = WWLists.getIndexClosestValue(data_time, time_start)
  index_end   = WWLists.getIndexClosestValue(data_time, time_end)
  data_time_subset  = data_time[index_start  : index_end]
  data_field_subset = data_field[index_start : index_end]
  return data_time_subset, data_field_subset

def loadSpectrum(filepath_file, spect_field, spect_comp="total"):
  with open(filepath_file, "r") as fp:
    dataset = fp.readlines()
    ## find row where header details are printed
    header_index = next((
      line_index+1
      for line_index, line_contents in enumerate(list(dataset))
      if "#" in line_contents
    ), None)
    if header_index is None: raise Exception("Error: no instances of '#' (indicating header file) found in:", filepath_file)
    ## read main dataset
    data = np.array([
      lines.strip().split() # remove leading/trailing whitespace + separate by whitespace-delimiter
      for lines in dataset[header_index:] # read from after header
    ])
    ## get the indices assiated with fields of interest
    iproc_index_ = 1
    if   "lgt" in spect_comp.lower(): field_index = 11 # longitudinal
    elif "trv" in spect_comp.lower(): field_index = 13 # transverse
    elif "tot" in spect_comp.lower():
      if "SpectFunctTot".lower() in dataset[5].lower():
        field_index = 15 # total = longitudinal + transverse
      else: field_index = 7 # if there is no spectrum decomposition
    else: raise Exception(f"Error: {spect_comp} is an invalid spectra component.")
    ## read fields from file
    data_k     = np.array(data[:, iproc_index_], dtype=float)
    data_power = np.array(data[:, field_index], dtype=float)
    if   "vel" in spect_field.lower(): data_power = data_power / 2
    elif "kin" in spect_field.lower(): data_power = data_power / 2
    elif "mag" in spect_field.lower(): data_power = data_power / (8 * np.pi)
    elif "cur" in spect_field.lower(): data_power = data_power / (4 * np.pi)
    # elif "rho" in spect_field.lower(): data_power = data_power
    else: raise Exception(f"Error: {spect_field} is an invalid spectra field. Failed to read and process:", filepath_file)
    return data_k, data_power

def loadAllSpectra(
    directory, spect_field, outputs_per_t_turb,
    spect_comp      = "total",
    file_start_time = 2,
    file_end_time   = np.inf,
    read_every      = 1,
    bool_verbose    = True
  ):
  if   "vel" in spect_field.lower(): file_end_str = "spect_velocity.dat"
  elif "kin" in spect_field.lower(): file_end_str = "spect_kinetic.dat"
  elif "mag" in spect_field.lower(): file_end_str = "spect_magnetic.dat"
  elif "cur" in spect_field.lower(): file_end_str = "spect_current.dat"
  # elif "rho" in spect_field.lower(): file_end_str = "spect_density.dat"
  else: raise Exception("Error: invalid spectra field-type provided:", spect_field)
  ## get list of spect-filenames in directory
  list_spectra_filenames = WWFnF.getFilesInDirectory(
    directory          = directory,
    filename_ends_with = file_end_str,
    loc_file_index     = -3,
    file_start_index   = outputs_per_t_turb * file_start_time,
    file_end_index     = outputs_per_t_turb * file_end_time
  )
  ## initialise list of spectra data
  list_t_turb        = []
  list_k_turb        = None
  spectra_group_t = []
  ## loop over each of the spectra file names
  for filename, _ in WWLists.loopListWithUpdates(list_spectra_filenames[::read_every], bool_verbose):
    ## convert file index to simulation time
    turb_time = float(filename.split("_")[-3]) / outputs_per_t_turb
    ## load data
    list_k_turb, list_power = loadSpectrum(
      filepath_file = f"{directory}/{filename}",
      spect_field   = spect_field,
      spect_comp    = spect_comp
    )
    ## store data
    spectra_group_t.append(list_power)
    list_t_turb.append(turb_time)
  ## return spectra data
  return {
    "list_t_turb"        : list_t_turb,
    "list_k_turb"        : list_k_turb,
    "spectra_group_t" : spectra_group_t,
  }

def getPlotsPerEddy_fromFlashLog(
    directory, max_num_t_turb,
    bool_verbose = True
  ):
  ## helper functions
  def getName(line):
    return line.split("=")[0].lower()
  def getValue(line):
    return line.split("=")[1].split("[")[0]
  ## search routine
  bool_tmax_found          = False
  bool_plot_interval_found = None
  with open(f"{directory}/{FileNames.FILENAME_FLASH_LOG}", "r") as fp:
    for line in fp.readlines()[::-1]:
      if ("tmax" in getName(line)) and ("dtmax" not in getName(line)):
        tmax = float(getValue(line))
        bool_tmax_found = True
      elif "plotfileintervaltime" in getName(line):
        plot_file_interval = float(getValue(line))
        bool_plot_interval_found = True
      if bool_tmax_found and bool_plot_interval_found:
        outputs_per_t_turb = tmax / plot_file_interval / max_num_t_turb
        ## a funny way to check this is also: abs(abs((outputs_per_t_turb % 1) - 0.5) - 0.5) < tol
        if abs(round(outputs_per_t_turb) - outputs_per_t_turb) > 1e-1:
          raise Exception(f"Error: the number of plt-files / t_turb (= {outputs_per_t_turb}) should be a whole number:\n\t", directory)
        if bool_verbose:
          print(f"The following has been read from {FileNames.FILENAME_FLASH_LOG}:")
          print("\t> 'tmax'".ljust(25),                 "=", tmax)
          print("\t> 'plotFileIntervalTime'".ljust(25), "=", plot_file_interval)
          print("\t> number of plt-files / t_turb".ljust(25),   "=", outputs_per_t_turb)
          print(f"\tAssuming the simulation has been setup to run for a max of {max_num_t_turb} t/t_turb.")
          print(" ")
        return int(outputs_per_t_turb)
  ## failed to read quantity
  raise Exception(f"Error: failed to read outputs_per_t_turb from {FileNames.FILENAME_FLASH_LOG}")

def computePlasmaConstants(Mach, k_turb, Re=None, Rm=None, Pm=None):
  ## Re and Pm have been defined
  if (Re is not None) and (Pm is not None):
    Re  = float(Re)
    Pm  = float(Pm)
    Rm  = Re * Pm
    nu  = round(Mach / (k_turb * Re), 5)
    eta = round(nu / Pm, 5)
  ## Rm and Pm have been defined
  elif (Rm is not None) and (Pm is not None):
    Rm  = float(Rm)
    Pm  = float(Pm)
    Re  = Rm / Pm
    eta = round(Mach / (k_turb * Rm), 5)
    nu  = round(eta * Pm, 5)
  ## error
  else: raise Exception(f"Error: insufficient plasma Reynolds numbers provided: Re = {Re}, Rm = {Rm}, Pm = {Rm}")
  return {
    "nu"  : nu,
    "eta" : eta,
    "Re"  : Re,
    "Rm"  : Rm,
    "Pm"  : Pm
  }

def computePlasmaNumbers(Re=None, Rm=None, Pm=None):
  ## Re and Pm have been defined
  if (Re is not None) and (Pm is not None):
    Rm = Re * Pm
  ## Rm and Pm have been defined
  elif (Rm is not None) and (Pm is not None):
    Re = Rm / Pm
  elif (Re is not None) and (Rm is not None):
    Pm = Rm / Re
  ## error
  else: raise Exception(f"Error: insufficient plasma Reynolds numbers provided: Re = {Re}, Rm = {Rm}, Pm = {Rm}")
  return {
    "Re"  : Re,
    "Rm"  : Rm,
    "Pm"  : Pm
  }

def getNumberFromString(string, var_name):
  string_lower = string.lower()
  var_name_lower = var_name.lower()
  return float(string_lower.replace(var_name_lower, "")) if var_name_lower in string_lower else None



## END OF LIBRARY