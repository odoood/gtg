#!/usr/bin/env bash

# Root cannot execute, it breaks graphical login (Changes /tmp permissions)
if [[ $UID -eq 0 ]]; then
    echo "GTG shouldn't be run as root, terminating"
    exit
fi

args=""
dataset="default"
norun=0
pydebug=0
title=""
explicit_title=0
prefix_prog=""
locale=""

datadir=tmp

# Interpret arguments. The ":" following the letter indicates that the opstring (optarg) needs a parameter specified. See also: https://stackoverflow.com/questions/18414054/rreading-optarg-for-optional-flags
while getopts "hdwns:t:l:p:" o;
do  case "$o" in
    d)   args="$args -d";;
    w)   pydebug=1;;
    n)   norun=1;;
    s)   dataset="$OPTARG";;
    t)   title="$OPTARG" explicit_title=1;;
    p)   prefix_prog="$OPTARG";;
    l)   locale="$OPTARG" ;;
    h|[?]) cat >&2 <<EOF
Usage: $0 [options] [-- ARG...]

Options:
 -h             Show this help message and exit
 -d             Enable debug mode, basically enables debug logging
 -w             Enable python warnings like deprecation warnings,
                and other python 3.7+ development mode features. Also see
                https://docs.python.org/3/library/devmode.html
 -n             Just run the build, don't actually run gtg
 -s DATASET     Use the dataset located in $PWD/$datadir/DATASET, or create a
                new dataset (with default data) by the same name if nonexistent.
                If special value 'local' or 'flatpak' is given, it imports your
                gtg user data from the respective source dirs so you can play
                with your tasks without fear of destroying them.
 -t TITLE       Set a custom title/program name to use.
                Use -t '' (empty) to un-set the title
                (default is: 'GTG (debug version — "<dataset>" dataset)')
 -l LOCALE      Run under the given LOCALE (use .po translation file)
 -p PREFIX_PROG Insert PREFIX_PROG before the application file when
                executing GTG. Example: -p 'python3 -m cProfile -o gtg.prof'
 -- ARG...      These arguments are passed to gtg as-is
                Use '-- --help' to get help for the application
EOF
         exit 1;;
    esac
done
args_array=("${@}")
extra_args=("${args_array[@]:$((OPTIND-1))}")

export XDG_DATA_HOME="$PWD/$datadir/$dataset/xdg/data"
export XDG_CACHE_HOME="$PWD/$datadir/$dataset/xdg/cache"
export XDG_CONFIG_HOME="$PWD/$datadir/$dataset/xdg/config"
export XDG_DATA_DIRS="$PWD/.local_build/install/share:${XDG_DATA_DIRS:-/usr/local/share:/usr/share}"

# Create data directory and copy test datasets
echo "Copying test datasets to ./$datadir/ ..."
mkdir -p $datadir
cp -rvut $datadir data/test-data/* || exit $?
echo Done.

# Import data from gtg user dirs if specified
if [[ "$dataset" = local || "$dataset" = flatpak ]]; then

    # Temporarily set error flag to quit if something goes wrong with import
    set -e

    proj_xml=$XDG_DATA_HOME/projects.xml

    case $dataset in
        local)
            datasrc=~/.local/share/gtg
            confsrc=~/.config/gtg
            ;;
        flatpak)
            datasrc=~/.var/app/org.gnome.GTG/data/gtg
            confsrc=~/.var/app/org.gnome.GTG/config/gtg
            ;;
    esac

    echo
    echo "Importing $dataset data..."

    mkdir -p $XDG_DATA_HOME/gtg
    cp -rvut $XDG_DATA_HOME/gtg $datasrc/*

    mkdir -p $XDG_CONFIG_HOME/gtg
    cp -rvut $XDG_CONFIG_HOME/gtg $confsrc/*

    echo Done.

    # Replace the paths in the project.xml file to point to XDG_DATA_HOME
    if [ -f "$proj_xml" ]; then
        sed -i 's:/home/.*/\.local/share:'"$XDG_DATA_HOME"':' "$proj_xml"
    fi

    set +e
fi

if [[ "$title" = "" ]] && [[ "$explicit_title" == 0 ]]; then
    title="GTG (debug version)"
    if [[ "$dataset" != "default" ]]; then
        title='GTG (debug version, "'$dataset'" dataset)'
    fi
fi
if ! [[ "$title" = "" ]]; then
    extra_args=('--title' "$title" "${extra_args[@]}")
fi

# Run make to build all
echo
echo "Running make..."
make all >/dev/null
echo "Build complete."
echo

# Set locale env args
if [ -n "$locale" ]; then

    echo "Locale set to \"$locale\""

    # Quit if the proper locale settings are not installed
    # otherwise warn if multiple are installed (use the first)
    langs=$(locale -a | grep "^$locale")

    if [ -z "$langs" ]; then
        echo "ERROR: No installed locale matches \"$locale\"" >&2
        exit 1
    fi

    lang=$(echo $langs | awk '{print $1}')

    echo "Using language $lang"
    echo

    if [ $(echo $langs | awk '{print NF}') -gt 1 ]; then
        echo "WARNING: multiple locales match \"$locale\", using \"$lang\""\
             '(specify region to avoid this, e.g. "de_DE" instead of "de")' >&2
    fi

    export LC_ALL=$lang LANGUAGE=$lang
fi

[[ "$norun" -ne 0 ]] && exit 0

echo "-----------------------------------------------------------------------"
echo "Running the development/debug version - using separate user directories"
echo "Your data is in the '$datadir' subdirectory with the '$dataset' dataset."
echo "-----------------------------------------------------------------------"

cd .local_build

# https://docs.python.org/3/library/devmode.html#devmode
[ "$pydebug" = 1 ] && export PYTHONDEVMODE=1

# double quoting args seems to prevent python script from picking up flag arguments correctly
# shellcheck disable=SC2086
$prefix_prog ./gtg ${args} "${extra_args[@]}" || exit $?
