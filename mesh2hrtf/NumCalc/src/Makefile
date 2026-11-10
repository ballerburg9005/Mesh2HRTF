CC=g++
CFLAGS=-c -std=c++11
SOURCES= NC_3dFunctions.cpp  NC_CommonFunctions.cpp  NC_Input.cpp \
         NC_PostProcessing.cpp NC_Addresses.cpp    NC_EquationSystem.cpp \
         NC_Main.cpp
OBJECTS=$(SOURCES:.cpp=.o)
EXECUTABLE=../bin/NumCalc
OPTIM=-O2
#DEBUG=-g

all: $(SOURCES) $(EXECUTABLE)

$(EXECUTABLE): $(OBJECTS) 
	$(CC) $(OBJECTS) $(LDFLAGS) $(OPTIM) -o $@


NC_CommonFunctions.o: NC_GMRES_Fallback.h

.cpp.o:
	$(CC) $(CFLAGS) $(DEBUG) $(OPTIM) $< 
clean:
	rm *.o
